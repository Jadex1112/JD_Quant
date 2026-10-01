"""Position Engine: positions, lots and P&L per account/instrument/deployment (Chapter 28)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from jdquant.core.events import Event, EventBus
from jdquant.core.types import ZERO
from jdquant.marketdata.instruments import InstrumentRegistry
from jdquant.oms.orders import Fill


class CostBasis(StrEnum):
    FIFO = "FIFO"
    AVERAGE = "AVERAGE"


@dataclass
class Lot:
    quantity: Decimal  # always positive; direction given by the position sign
    price: Decimal
    opened_at: datetime
    fill_id: str


@dataclass
class Position:
    account_id: str
    instrument_id: str
    deployment_id: str
    multiplier: Decimal
    quantity: Decimal = ZERO
    average_entry_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO
    opened_at: datetime | None = None
    last_fill_at: datetime | None = None
    lots: deque[Lot] = field(default_factory=deque)

    @property
    def is_open(self) -> bool:
        return self.quantity != 0

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        """BR-28-03, linear instruments."""
        return self.quantity * (price - self.average_entry_price) * self.multiplier


class PositionEngine:
    def __init__(
        self, bus: EventBus, instruments: InstrumentRegistry, cost_basis: CostBasis = CostBasis.FIFO
    ):
        self._bus = bus
        self._instruments = instruments
        self.cost_basis = cost_basis
        self._positions: dict[tuple[str, str, str], Position] = {}
        bus.subscribe("order.fill", self._on_fill)

    def _on_fill(self, event: Event) -> None:
        self.apply_fill(event.payload["fill"])

    def apply_fill(self, fill: Fill, *, publish: bool = True) -> Decimal:
        """Apply a fill and return the realized P&L it produced (FR-28001, FR-28004, FR-28006)."""
        position = self.get_or_create(fill.account_id, fill.instrument_id, fill.deployment_id)
        signed = fill.quantity * fill.side.sign
        realized = ZERO
        remaining = abs(signed)
        direction = 1 if signed > 0 else -1

        if position.quantity != 0 and (position.quantity > 0) != (signed > 0):
            closing = min(remaining, abs(position.quantity))
            realized = self._close(position, closing, fill.price)
            remaining -= closing
        if remaining > 0:
            self._open(position, remaining * direction, fill)

        position.realized_pnl += realized
        position.fees_paid += fill.fee
        position.last_fill_at = fill.exchange_ts
        if position.quantity == 0:
            position.average_entry_price = ZERO
            position.lots.clear()
            position.opened_at = None

        if not publish:
            return realized
        self._bus.publish(
            "position.updated",
            {
                "account_id": position.account_id,
                "instrument_id": position.instrument_id,
                "deployment_id": position.deployment_id,
                "quantity": position.quantity,
                "average_entry_price": position.average_entry_price,
                "multiplier": position.multiplier,
                "realized_delta": realized,
                "fee": fill.fee,
                "fill_id": fill.fill_id,
            },
            producer="positions",
            partition_key=position.account_id,
        )
        return realized

    def _open(self, position: Position, signed_qty: Decimal, fill: Fill) -> None:
        if position.quantity == 0:
            position.opened_at = fill.exchange_ts
        new_abs = abs(position.quantity) + abs(signed_qty)
        position.average_entry_price = (
            position.average_entry_price * abs(position.quantity) + fill.price * abs(signed_qty)
        ) / new_abs
        position.quantity += signed_qty
        position.lots.append(Lot(abs(signed_qty), fill.price, fill.exchange_ts, fill.fill_id))

    def _close(self, position: Position, quantity: Decimal, exit_price: Decimal) -> Decimal:
        sign = 1 if position.quantity > 0 else -1
        realized = ZERO
        if self.cost_basis is CostBasis.AVERAGE:
            realized = quantity * (exit_price - position.average_entry_price) * sign * position.multiplier
            self._consume_lots(position, quantity)
        else:
            left = quantity
            while left > 0:
                lot = position.lots[0]
                used = min(left, lot.quantity)
                realized += used * (exit_price - lot.price) * sign * position.multiplier
                lot.quantity -= used
                left -= used
                if lot.quantity == 0:
                    position.lots.popleft()
        position.quantity -= quantity * sign
        if self.cost_basis is CostBasis.FIFO and position.lots:
            total = sum((lot.quantity for lot in position.lots), ZERO)
            position.average_entry_price = (
                sum((lot.quantity * lot.price for lot in position.lots), ZERO) / total
            )
        return realized

    @staticmethod
    def _consume_lots(position: Position, quantity: Decimal) -> None:
        left = quantity
        while left > 0 and position.lots:
            lot = position.lots[0]
            used = min(left, lot.quantity)
            lot.quantity -= used
            left -= used
            if lot.quantity == 0:
                position.lots.popleft()

    def get_or_create(self, account_id: str, instrument_id: str, deployment_id: str) -> Position:
        key = (account_id, instrument_id, deployment_id)
        if key not in self._positions:
            multiplier = self._instruments.get(instrument_id).contract_multiplier
            self._positions[key] = Position(account_id, instrument_id, deployment_id, multiplier)
        return self._positions[key]

    def positions(
        self, *, account_id: str | None = None, deployment_id: str | None = None, open_only: bool = True
    ) -> list[Position]:
        return [
            p
            for p in self._positions.values()
            if (account_id is None or p.account_id == account_id)
            and (deployment_id is None or p.deployment_id == deployment_id)
            and (not open_only or p.is_open)
        ]

    def net_quantity(self, account_id: str, instrument_id: str) -> Decimal:
        return sum(
            (
                p.quantity
                for p in self._positions.values()
                if p.account_id == account_id and p.instrument_id == instrument_id
            ),
            ZERO,
        )
