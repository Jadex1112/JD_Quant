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


OWNER = {"email": "owner@example.com", "display_name": "Owner", "password": "correct-horse-battery"}


def make_app(platform, *, enforce_mfa: bool = False):
    from jdquant.api.app import create_app
    from jdquant.api.context import Settings

    return create_app(platform, settings=Settings(enforce_mfa_for_privileged=enforce_mfa))


def login_client(platform, *, enforce_mfa: bool = False):
    """Test client authenticated as the bootstrap owner (all roles)."""
    from fastapi.testclient import TestClient

    client = TestClient(make_app(platform, enforce_mfa=enforce_mfa))
    assert client.post("/api/v1/setup", json=OWNER).status_code == 201
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return client


@pytest.fixture
def app_ctx():
    """Owner-authenticated API client with the demo feed: (client, context, platform, clock)."""
    from fastapi.testclient import TestClient

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings

    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock)
    set_quote(platform, BTC, "49999", "50001")
    app = create_app(platform, settings=Settings(enforce_mfa_for_privileged=False, demo_feed=True))
    client = TestClient(app)
    assert client.post("/api/v1/setup", json=OWNER).status_code == 201
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return client, app.state.ctx, platform, clock
