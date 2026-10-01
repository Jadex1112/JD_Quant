"""Instrument reference data (Chapter 20.3–20.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from jdquant.core.errors import NotFoundError
from jdquant.core.types import Side


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX = "FX"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERPETUAL = "CRYPTO_PERPETUAL"
    CRYPTO_FUTURE = "CRYPTO_FUTURE"
    COMMODITY = "COMMODITY"
    FIXED_INCOME = "FIXED_INCOME"
    INDEX = "INDEX"


class InstrumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    asset_class: AssetClass
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    lot_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal | None = None
    min_notional: Decimal | None = None
    contract_multiplier: Decimal = Decimal(1)
    status: InstrumentStatus = InstrumentStatus.ACTIVE
    aliases: tuple[tuple[str, str], ...] = field(default=())
    expiry: datetime | None = None  # futures: last trading time
    underlying: str | None = None  # futures: the root symbol, e.g. GOLDM for MCX:GOLDM26JANFUT
    shortable: bool = False  # margin products (forex, CFDs) that can be sold without holding them

    @property
    def is_future(self) -> bool:
        return self.expiry is not None

    @property
    def can_short(self) -> bool:
        return self.shortable or self.is_future

    @property
    def instrument_id(self) -> str:
        return f"{self.venue}:{self.symbol}"

    def is_price_aligned(self, price: Decimal) -> bool:
        return price > 0 and price % self.tick_size == 0

    def is_quantity_aligned(self, quantity: Decimal) -> bool:
        return quantity > 0 and quantity % self.lot_size == 0

    def round_price_passive(self, price: Decimal, side: Side) -> Decimal:
        """Round to a valid tick in the passive direction (FR-21006)."""
        rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
        return (price / self.tick_size).to_integral_value(rounding=rounding) * self.tick_size

    def round_quantity_down(self, quantity: Decimal) -> Decimal:
        return (quantity / self.lot_size).to_integral_value(rounding=ROUND_FLOOR) * self.lot_size

    def notional(self, quantity: Decimal, price: Decimal) -> Decimal:
        return quantity * price * self.contract_multiplier


class InstrumentRegistry:
    def __init__(self) -> None:
        self._instruments: dict[str, Instrument] = {}
        self._aliases: dict[tuple[str, str], str] = {}

    def add(self, instrument: Instrument) -> None:
        self._instruments[instrument.instrument_id] = instrument
        for source, symbol in instrument.aliases:
            self._aliases[(source, symbol)] = instrument.instrument_id

    def get(self, instrument_id: str) -> Instrument:
        try:
            return self._instruments[instrument_id]
        except KeyError:
            raise NotFoundError("INSTRUMENT_NOT_FOUND", f"unknown instrument {instrument_id}") from None

    def resolve(self, source: str, symbol: str) -> Instrument:
        instrument_id = self._aliases.get((source, symbol))
        if instrument_id is None:
            raise NotFoundError("INSTRUMENT_NOT_FOUND", f"cannot resolve {source}:{symbol}")
        return self._instruments[instrument_id]

    def search(self, query: str = "", asset_class: AssetClass | None = None) -> list[Instrument]:
        q = query.upper()
        return [
            i
            for i in self._instruments.values()
            if (not q or q in i.instrument_id.upper() or q in i.base_asset.upper())
            and (asset_class is None or i.asset_class is asset_class)
        ]

    def all(self) -> list[Instrument]:
        return list(self._instruments.values())
