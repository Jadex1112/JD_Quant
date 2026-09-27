from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from jdquant.core.clock import SimulatedClock
from jdquant.core.types import Side
from jdquant.marketdata.records import Quote
from jdquant.oms.orders import OrderRequest, OrderType
from jdquant.platform import PAPER_ACCOUNT_ID, Platform, build_paper_platform

BTC = "BINANCE:BTCUSDT"
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> SimulatedClock:
    return SimulatedClock(T0)


@pytest.fixture
def platform(clock: SimulatedClock) -> Platform:
    p = build_paper_platform(clock)
    set_quote(p, BTC, "49999", "50001")
    return p


def set_quote(p: Platform, instrument_id: str, bid: str, ask: str) -> None:
    p.market.on_quote(Quote(instrument_id, p.clock.now(), Decimal(bid), Decimal(1), Decimal(ask), Decimal(1)))


def advance(clock: SimulatedClock, **kwargs) -> None:
    clock.set(clock.now() + timedelta(**kwargs))


def market(side: Side, qty: str, instrument_id: str = BTC, **kw) -> OrderRequest:
    return OrderRequest(PAPER_ACCOUNT_ID, instrument_id, side, OrderType.MARKET, Decimal(qty), **kw)


def limit(side: Side, qty: str, price: str, instrument_id: str = BTC, **kw) -> OrderRequest:
    return OrderRequest(
        PAPER_ACCOUNT_ID, instrument_id, side, OrderType.LIMIT, Decimal(qty), limit_price=Decimal(price), **kw
    )
