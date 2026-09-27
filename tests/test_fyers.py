"""Fyers login/session handling, symbol master, Indian charges and NSE hours."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import pytest
from conftest import T0, advance
from fake_venues import FakeFyers, fake_jwt

from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ConnectionManager, ConnectionStatus
from jdquant.connectivity.poller import VenuePoller
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.markets.india import IST, IndiaEquityFees, NseCalendar, Product
from jdquant.oms.orders import OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.secrets import SecretBox, SecretStore
from jdquant.strategy.runner import DeploymentRunner

KEY = None


def _secrets(store):
    global KEY
    from cryptography.fernet import Fernet

    KEY = KEY or Fernet.generate_key()
    return SecretStore(store, SecretBox(KEY))


def _setup(store=None, fake=None, login=True):
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=store or Store(":memory:"))
    fake = fake or FakeFyers(now=T0)
    manager = ConnectionManager(platform, platform.store, _secrets(platform.store), lambda v: fake.client())
    conn = manager.create(
        name="Fyers",
        venue="FYERS",
        environment=Environment.PRODUCTION,
        actor="t",
        api_key=fake.app_id,
        api_secret=fake.secret,
        base_currency="INR",
    )
    if login:
        conn = manager.complete_login(_state(manager, conn.connection_id), fake.auth_code)
    return platform, manager, conn, fake


def _state(manager, connection_id):
    url = manager.begin_login(connection_id, "http://localhost:8000/api/v1/connections/fyers/callback")
    query = dict(parse_qsl(urlsplit(url).query))
    assert url.startswith("https://api-t1.fyers.in/api/v3/generate-authcode?")
    assert query["client_id"] == "XA1234-100" and query["response_type"] == "code"
    return query["state"]


def test_connection_waits_for_login_then_registers_live_account():
    platform, manager, conn, _ = _setup(login=False)
    assert conn.status is ConnectionStatus.LOGIN_REQUIRED and conn.account_id is None
    conn = manager.complete_login(_state(manager, conn.connection_id), "good-code")
    assert conn.status is ConnectionStatus.CONNECTED
    account = platform.trading.get_account(conn.account_id)
    assert account.mode.value == "LIVE" and account.base_currency == "INR"
    assert manager.adapter_for_account(conn.account_id).fetch_balances()[0].free == Decimal(100_000)


def test_symbol_master_keeps_nse_equities_only():
    platform, _, conn, _ = _setup()
    assert conn.instrument_count == 2
    assert platform.instruments.get("NSE:SBIN-EQ").tick_size == Decimal("0.05")
    assert platform.instruments.get("NSE:RELIANCE-EQ").tick_size == Decimal("0.1")
    with pytest.raises(PlatformError):
        platform.instruments.get("NSE:NIFTY50-INDEX")


def test_login_state_is_single_use_and_bad_codes_fail():
    _, manager, conn, _ = _setup(login=False)
    state = _state(manager, conn.connection_id)
    with pytest.raises(PlatformError, match="LOGIN_FAILED"):
        manager.complete_login(state, "wrong-code")
    with pytest.raises(PlatformError, match="LOGIN_STATE_INVALID"):
        manager.complete_login(state, "good-code")  # the state was consumed
    with pytest.raises(PlatformError, match="LOGIN_STATE_INVALID"):
        manager.complete_login("forged", "good-code")


def test_fyers_has_no_test_environment():
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    manager = ConnectionManager(
        platform, platform.store, _secrets(platform.store), lambda v: FakeFyers().client()
    )
    with pytest.raises(PlatformError, match="ENVIRONMENT_UNSUPPORTED"):
        manager.create(
            name="f", venue="FYERS", environment=Environment.TESTNET, actor="t", api_key="a", api_secret="b"
        )


def test_expired_session_renews_with_saved_pin():
    platform, manager, conn, fake = _setup()
    manager.set_pin(conn.connection_id, "1234")
    adapter = manager.adapter_for_account(conn.account_id)
    fake.token = fake_jwt(T0.replace(hour=23, minute=59))  # the venue will only accept a renewed token
    advance(platform.clock, seconds=15 * 3600)  # past the first token's expiry
    assert adapter.fetch_balances()
    assert adapter.access_token == fake.token


def test_expired_session_without_pin_asks_the_user_to_sign_in():
    platform, manager, conn, _ = _setup()
    poller = VenuePoller(platform, manager, DeploymentRunner(platform, manager.data_source_for))
    advance(platform.clock, seconds=15 * 3600)
    poller.tick()
    assert manager.get(conn.connection_id).status is ConnectionStatus.LOGIN_REQUIRED
    assert manager.data_source_for("NSE:SBIN-EQ") is None


def test_session_survives_restart(tmp_path):
    db = tmp_path / "jq.db"
    platform, manager, conn, fake = _setup(store=Store(db))
    platform.store.close()
    restarted = build_paper_platform(SimulatedClock(T0), store=Store(db))
    again = ConnectionManager(restarted, restarted.store, _secrets(restarted.store), lambda v: fake.client())
    again.load_all()
    adapter = again.adapter_for_account(conn.account_id)
    assert adapter.is_ready() and adapter.fetch_balances()
    assert restarted.instruments.get("NSE:SBIN-EQ")


def test_fills_carry_indian_charges():
    platform, manager, conn, _ = _setup()
    manager.set_watchlist(conn.connection_id, ["NSE:SBIN-EQ"])
    VenuePoller(platform, manager, DeploymentRunner(platform, manager.data_source_for)).tick()
    order = platform.oms.submit(
        OrderRequest(conn.account_id, "NSE:SBIN-EQ", Side.BUY, OrderType.MARKET, Decimal(10))
    )
    VenuePoller(platform, manager, DeploymentRunner(platform, manager.data_source_for)).tick()
    assert order.status is OrderStatus.FILLED
    fill = platform.oms.fills[-1]
    assert fill.fee_asset == "INR" and fill.fee > 0
    assert fill.exchange_ts == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)  # 14:30 IST


def test_daily_history_excludes_the_forming_bar():
    platform, manager, _, _ = _setup()
    adapter = manager.data_source_for("NSE:SBIN-EQ")
    candles = adapter.fetch_candles(platform.instruments.get("NSE:SBIN-EQ"), 86400, 30)
    assert len(candles) == 30
    assert all(c.close_ts <= platform.clock.now() for c in candles)
    assert candles[-1].close == Decimal(801)


def test_delivery_and_intraday_charges():
    delivery = IndiaEquityFees(product=Product.CNC).breakdown(Side.BUY, Decimal(100_000))
    assert delivery["brokerage"] == Decimal(20)  # 0.3% capped at ₹20
    assert delivery["stt"] == Decimal(100) and delivery["stamp"] == Decimal(15)
    assert delivery["total"] == Decimal("142.22")
    intraday_sell = IndiaEquityFees(product=Product.INTRADAY).breakdown(Side.SELL, Decimal(100_000))
    assert intraday_sell["stt"] == Decimal(25) and intraday_sell["stamp"] == 0
    assert intraday_sell["total"] == Decimal("52.22")
    small = IndiaEquityFees(product=Product.INTRADAY).breakdown(Side.BUY, Decimal(10_000))
    assert small["brokerage"] == Decimal(3)  # 0.03% below the cap


def test_nse_session_hours():
    cal = NseCalendar(holidays=frozenset({datetime(2026, 1, 26).date()}))
    monday_10 = datetime(2026, 1, 5, 10, 0, tzinfo=IST)
    assert cal.is_open(monday_10)
    assert not cal.is_open(monday_10.replace(hour=9, minute=0))
    assert not cal.is_open(datetime(2026, 1, 10, 11, 0, tzinfo=IST))  # Saturday
    assert not cal.is_open(datetime(2026, 1, 26, 11, 0, tzinfo=IST))  # holiday
    assert cal.past_intraday_cutoff(monday_10.replace(hour=15, minute=12))
    friday_close = datetime(2026, 1, 9, 15, 45, tzinfo=IST)
    assert cal.next_open(friday_close) == datetime(2026, 1, 12, 9, 15, tzinfo=IST)
    assert cal.next_open(monday_10 - timedelta(hours=2)) == datetime(2026, 1, 5, 9, 15, tzinfo=IST)


def test_login_through_the_api_and_browser_redirect(platform):
    from conftest import OWNER
    from fastapi.testclient import TestClient

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    fake = FakeFyers(now=T0)
    context = build_context(
        Settings(enforce_mfa_for_privileged=False), platform, http_factory=lambda v: fake.client()
    )
    client = TestClient(create_app(context=context))
    client.post("/api/v1/setup", json=OWNER)
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"

    created = client.post(
        "/api/v1/connections",
        json={"name": "My Fyers", "venue": "FYERS", "environment": "PRODUCTION", "api_key": fake.app_id,
              "api_secret": fake.secret, "base_currency": "INR", "settings": {"product": "CNC"}},
    ).json()  # fmt: skip
    assert created["status"] == "LOGIN_REQUIRED" and created["requires_login"]
    assert created["markets"] == ["NSE"]
    login = client.post(f"/api/v1/connections/{created['connection_id']}:login").json()
    assert login["redirect_uri"] == "http://testserver/api/v1/connections/oauth/callback"
    state = dict(parse_qsl(urlsplit(login["login_url"]).query))["state"]

    browser = TestClient(create_app(context=context), follow_redirects=False)  # no session cookie
    bad = browser.get(
        "/api/v1/connections/oauth/callback", params={"s": "ok", "auth_code": "x", "state": "nope"}
    )
    assert bad.status_code == 303 and "login=failed" in bad.headers["location"]
    ok = browser.get(
        "/api/v1/connections/oauth/callback",
        params={"s": "ok", "code": "200", "auth_code": fake.auth_code, "state": state},
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/connections?login=ok"

    conn = client.get("/api/v1/connections").json()[0]
    assert conn["status"] == "CONNECTED" and conn["account_id"] and conn["session_expires_at"]
    accounts = {a["account_id"]: a for a in client.get("/api/v1/accounts").json()}
    assert accounts[conn["account_id"]]["markets"] == ["NSE"]
    assert (
        client.put(f"/api/v1/connections/{conn['connection_id']}/pin", json={"pin": "12ab"}).status_code
        == 422
    )
    assert (
        client.put(f"/api/v1/connections/{conn['connection_id']}/pin", json={"pin": "1234"}).status_code
        == 200
    )
    events = [e["action"] for e in client.get("/api/v1/audit-events").json()]
    assert "connection.login" in events and "connection.pin.set" in events
    assert "1234" not in str(client.get("/api/v1/audit-events").json())
