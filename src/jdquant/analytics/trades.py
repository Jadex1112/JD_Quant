"""Round-trip trade construction with FIFO matching (FR-25042, FR-31001)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from jdquant.core.types import Side
from jdquant.oms.orders import Fill


@dataclass(frozen=True)
class RoundTrip:
    instrument_id: str
    direction: Side
    quantity: Decimal
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees


@dataclass
class _Open:
    side: Side
    quantity: Decimal
    price: Decimal
    time: datetime
    fee_per_unit: Decimal


def round_trips(fills: Iterable[Fill], multipliers: dict[str, Decimal] | None = None) -> list[RoundTrip]:
    multipliers = multipliers or {}
    books: dict[str, deque[_Open]] = {}
    trips: list[RoundTrip] = []
    for fill in sorted(fills, key=lambda f: f.exchange_ts):
        book = books.setdefault(fill.instrument_id, deque())
        mult = multipliers.get(fill.instrument_id, Decimal(1))
        remaining = fill.quantity
        exit_fee_per_unit = fill.fee / fill.quantity
        while remaining > 0 and book and book[0].side is not fill.side:
            entry = book[0]
            qty = min(remaining, entry.quantity)
            trips.append(
                RoundTrip(
                    instrument_id=fill.instrument_id,
                    direction=entry.side,
                    quantity=qty,
                    entry_time=entry.time,
                    exit_time=fill.exchange_ts,
                    entry_price=entry.price,
                    exit_price=fill.price,
                    gross_pnl=qty * (fill.price - entry.price) * entry.side.sign * mult,
                    fees=qty * (entry.fee_per_unit + exit_fee_per_unit),
                )
            )
            entry.quantity -= qty
            remaining -= qty
            if entry.quantity == 0:
                book.popleft()
        if remaining > 0:
            book.append(_Open(fill.side, remaining, fill.price, fill.exchange_ts, exit_fee_per_unit))
    return trips
