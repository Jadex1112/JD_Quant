"""Canonical market data record types (Chapter 20.3.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from jdquant.core.types import Side


@dataclass(frozen=True)
class Trade:
    instrument_id: str
    exchange_ts: datetime
    price: Decimal
    quantity: Decimal
    aggressor_side: Side | None = None
    trade_id: str = ""


@dataclass(frozen=True)
class Quote:
    instrument_id: str
    exchange_ts: datetime
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid_price + self.ask_price) / 2

    @property
    def is_crossed(self) -> bool:
        return self.bid_price >= self.ask_price


@dataclass(frozen=True)
class Candle:
    instrument_id: str
    interval_seconds: int
    open_ts: datetime
    close_ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal = Decimal(0)
    trade_count: int = 0
    vwap: Decimal | None = None
    is_closed: bool = True
