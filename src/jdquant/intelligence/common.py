"""Helpers shared by the intelligence engines."""

from __future__ import annotations

import statistics
from datetime import date, datetime

from jdquant.marketdata.instruments import Instrument
from jdquant.markets.sessions import FxSession, session_for

CURRENCY_SIGNS = {"INR": "₹", "USD": "$", "USDT": "$", "USDC": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def session_day(instrument: Instrument | None, at: datetime) -> date:
    """The trading day a timestamp belongs to (forex days roll at 17:00 New York)."""
    if instrument is None:
        return at.date()
    session = session_for(instrument)
    local = at.astimezone(session.tz)
    if isinstance(session, FxSession):
        return session._trading_day(local)
    return local.date()


def money(instrument: Instrument | None, value: float | None) -> str:
    if value is None:
        return "—"
    sign = CURRENCY_SIGNS.get(instrument.quote_asset if instrument else "", "")
    decimals = price_decimals(instrument)
    return f"{sign}{value:,.{decimals}f}"


def price_decimals(instrument: Instrument | None) -> int:
    if instrument is None:
        return 2
    tick = instrument.tick_size.normalize()
    return max(0, -tick.as_tuple().exponent) if tick > 0 else 2


def quantity(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}"
    return f"{value:,.4g}"


def tick_size(instrument: Instrument | None, fallback: float = 0.01) -> float:
    if instrument is None or instrument.tick_size <= 0:
        return fallback
    return float(instrument.tick_size)


def median(values, default: float = 0.0) -> float:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else default


def mean(values, default: float = 0.0) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else default
