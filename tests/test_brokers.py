"""Zerodha Kite, Upstox, Angel One and Dhan (NSE stocks and ETFs) and Delta Exchange India (crypto perps)."""

import json
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import pytest
from conftest import T0
from fake_brokers import ASK, BID, FakeAngel, FakeDelta, FakeDhan, FakeKite, FakeNeo, FakeUpstox, dhan_token

from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ConnectionManager, ConnectionStatus
from jdquant.connectivity.poller import VenuePoller
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.marketdata.instruments import AssetClass
from jdquant.oms.orders import OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.secrets import SecretBox, SecretStore
from jdquant.strategy.runner import DeploymentRunner

KEY = None
REDIRECT = "http://localhost:8000/api/v1/connections/oauth/callback"
SBIN = "NSE:SBIN-EQ"


def _secrets(store):
    global KEY
    from cryptography.fernet import Fernet

    KEY = KEY or Fernet.generate_key()
    return SecretStore(store, SecretBox(KEY))


def _credentials(venue, fake):
    if venue in ("KITE", "UPSTOX"):
        return fake.key, fake.secret
    if venue == "ANGELONE":
        secret = {"client_code": fake.client_code, "pin": fake.pin, "totp_secret": fake.totp_secret}
        return fake.key, json.dumps(secret)
    if venue == "KOTAKNEO":
        secret = {"mobile": fake.mobile, "ucc": fake.ucc, "mpin": fake.mpin, "totp_secret": fake.totp_secret}
        return fake.key, json.dumps(secret)
    return fake.client_id, fake.token  # DHAN


FAKES = {"KITE": FakeKite, "UPSTOX": FakeUpstox, "ANGELONE": FakeAngel, "DHAN": FakeDhan, "KOTAKNEO": FakeNeo}
INDIAN = list(FAKES)


def _state(url: str) -> str:
    query = dict(parse_qsl(urlsplit(url).query))
    if "redirect_params" in query:  # Kite returns these to the redirect URL
        return dict(parse_qsl(query["redirect_params"]))["state"]
    return query["state"]


def _setup(venue, *, product="CNC", store=None, fake=None):
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=store or Store(":memory:"))
    fake = fake or FAKES[venue](T0)
    manager = ConnectionManager(platform, platform.store, _secrets(platform.store), lambda v: fake.client())
    key, secret = _credentials(venue, fake)
    conn = manager.create(
        name=venue.title(),
        venue=venue,
        environment=Environment.PRODUCTION,
        actor="t",
        api_key=key,
        api_secret=secret,
        base_currency="INR",
        settings={"product": product},
    )
    if conn.status is ConnectionStatus.LOGIN_REQUIRED:
        url = manager.begin_login(conn.connection_id, REDIRECT)
        code = fake.request_token if venue == "KITE" else fake.code
        conn = manager.complete_login(_state(url), code)
    manager.set_watchlist(conn.connection_id, [SBIN])
    _poll(platform, manager)
    return platform, manager, conn, fake


def _poll(platform, manager):
    VenuePoller(platform, manager, DeploymentRunner(platform, manager.data_source_for)).tick()


def _order(platform, conn, side=Side.BUY, qty=10, limit=None, instrument=SBIN):
    return platform.oms.submit(
        OrderRequest(
            conn.account_id,
            instrument,
            side,
            OrderType.LIMIT if limit else OrderType.MARKET,
            Decimal(qty),
            limit_price=Decimal(limit) if limit else None,
        )
    )


@pytest.mark.parametrize("venue", INDIAN)
def test_connects_and_loads_nse_stocks_and_etfs(venue):
    platform, manager, conn, _ = _setup(venue)
    assert conn.status is ConnectionStatus.CONNECTED and conn.instrument_count == 2
    sbin, gold = platform.instruments.get(SBIN), platform.instruments.get("NSE:GOLDBEES-EQ")
    assert sbin.asset_class is AssetClass.EQUITY and sbin.tick_size == Decimal("0.05")
    assert gold.asset_class is AssetClass.ETF and gold.tick_size == Decimal("0.01")
    account = platform.trading.get_account(conn.account_id)
    assert account.mode.value == "LIVE" and account.base_currency == "INR"
    assert manager.adapter_for_account(conn.account_id).fetch_balances()[0].free == Decimal(100000)
    assert platform.market.reference_price(SBIN) == (BID + ASK) / 2


@pytest.mark.parametrize("venue", INDIAN)
def test_orders_fill_cancel_and_carry_indian_charges(venue):
    platform, manager, conn, fake = _setup(venue)
    market = _order(platform, conn)
    _poll(platform, manager)
    assert market.status is OrderStatus.FILLED
    fill = platform.oms.fills[-1]
    assert fill.price == ASK and fill.quantity == 10 and fill.fee_asset == "INR" and fill.fee > 0
    placed = next(o for o in fake.orders.values())
    assert market.client_order_id.replace("-", "").endswith(placed["tag"])  # findable after a timeout
    resting = _order(platform, conn, limit="795")
    assert resting.status is OrderStatus.OPEN
    fake.fill(resting.venue_order_id)
    _poll(platform, manager)
    assert resting.status is OrderStatus.FILLED and platform.oms.fills[-1].price == Decimal(795)
    doomed = _order(platform, conn, limit="790")
    platform.oms.cancel(doomed.order_id)
    assert doomed.status is OrderStatus.CANCELED


@pytest.mark.parametrize("venue", INDIAN)
def test_product_follows_the_connection(venue):
    platform, _, conn, fake = _setup(venue, product="INTRADAY")
    _order(platform, conn)
    sent = next(r for r in reversed(fake.requests) if r.method == "POST" and "order" in r.url.path.lower())
    if venue == "KOTAKNEO":
        body = json.loads(dict(parse_qsl(sent.content.decode()))["jData"])
    else:
        body = dict(parse_qsl(sent.content.decode())) if venue == "KITE" else json.loads(sent.content)
    product = body.get("product") or body.get("producttype") or body.get("productType") or body.get("pc")
    expected = {"KITE": "MIS", "UPSTOX": "I", "ANGELONE": "INTRADAY", "DHAN": "INTRADAY", "KOTAKNEO": "MIS"}
    assert product == expected[venue]


@pytest.mark.parametrize("venue", [v for v in INDIAN if v != "KOTAKNEO"])
def test_candles_exclude_the_forming_bar(venue):
    platform, manager, _, _ = _setup(venue)
    adapter = manager.data_source_for(SBIN)
    candles = adapter.fetch_candles(platform.instruments.get(SBIN), 900 if venue != "DHAN" else 900, 20)
    assert len(candles) == 20 and all(c.close_ts <= T0 for c in candles)
    assert candles[1].open_ts - candles[0].open_ts == timedelta(minutes=15)


@pytest.mark.parametrize("venue", INDIAN)
def test_references_survive_a_restart(venue, tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    platform, _, conn, fake = _setup(venue, store=store)
    clock = SimulatedClock(T0)
    again = build_paper_platform(clock, store=store)
    manager = ConnectionManager(again, store, _secrets(store), lambda v: fake.client())
    manager.load_all()
    _poll(again, manager)  # fresh prices: the risk checks need them
    assert manager.connections[conn.connection_id].status is not ConnectionStatus.LOGIN_REQUIRED
    order = again.oms.submit(
        OrderRequest(conn.account_id, SBIN, Side.BUY, OrderType.LIMIT, Decimal(1), limit_price=Decimal(780))
    )
    assert order.status is OrderStatus.OPEN


def test_kite_login_uses_the_checksum_and_the_callback_route(platform):
    fake = FakeKite(T0)
    clock = SimulatedClock(T0)
    p = build_paper_platform(clock, store=Store(":memory:"))
    manager = ConnectionManager(p, p.store, _secrets(p.store), lambda v: fake.client())
    conn = manager.create(
        name="Kite",
        venue="KITE",
        environment=Environment.PRODUCTION,
        actor="t",
        api_key=fake.key,
        api_secret=fake.secret,
    )
    url = manager.begin_login(conn.connection_id, REDIRECT)
    assert url.startswith("https://kite.zerodha.com/connect/login?v=3&api_key=kitekey&redirect_params=")
    with pytest.raises(PlatformError, match="LOGIN_FAILED"):
        manager.complete_login(_state(url), "wrong-token")
    with pytest.raises(PlatformError):
        manager.complete_login("not-a-state", fake.request_token)
    ok = manager.complete_login(_state(manager.begin_login(conn.connection_id, REDIRECT)), fake.request_token)
    assert ok.status is ConnectionStatus.CONNECTED
    assert ok.session_expires_at.astimezone().hour is not None  # next 06:00 IST
    with pytest.raises(PlatformError):
        ConnectionManager(p, p.store, _secrets(p.store), lambda v: fake.client()).create(
            name="x", venue="KITE", environment=Environment.TESTNET, actor="t", api_key="a", api_secret="b"
        )


def test_upstox_token_exchange_repeats_the_redirect_uri():
    _, _, conn, fake = _setup("UPSTOX")
    assert fake.redirects == [REDIRECT] and conn.status is ConnectionStatus.CONNECTED


def test_angel_one_signs_in_by_itself_and_again_when_the_session_ends():
    platform, manager, conn, fake = _setup("ANGELONE")
    assert fake.logins >= 1
    before = fake.logins
    fake.jwt = "jwt-2"  # the broker ended the session
    order = _order(platform, conn)
    assert order.status is OrderStatus.OPEN or order.status is OrderStatus.FILLED
    assert fake.logins == before + 1


def test_angel_one_needs_all_its_credentials():
    with pytest.raises(PlatformError):
        clock = SimulatedClock(T0)
        p = build_paper_platform(clock, store=Store(":memory:"))
        ConnectionManager(p, p.store, _secrets(p.store), lambda v: FakeAngel(T0).client()).create(
            name="a",
            venue="ANGELONE",
            environment=Environment.PRODUCTION,
            actor="t",
            api_key="k",
            api_secret=json.dumps({"client_code": "A1"}),
        )


def test_dhan_expired_token_asks_for_a_new_one():
    platform, manager, conn, fake = _setup("DHAN")
    platform.clock.set(T0 + timedelta(hours=21))  # the 24-hour token (20 h left at setup) has expired
    _poll(platform, manager)
    assert manager.connections[conn.connection_id].status is ConnectionStatus.LOGIN_REQUIRED
    fake.now = platform.clock.now()
    fake.token = dhan_token(platform.clock.now() + timedelta(hours=24))
    manager.rotate(conn.connection_id, fake.client_id, fake.token)
    _poll(platform, manager)
    assert manager.connections[conn.connection_id].status is ConnectionStatus.CONNECTED
    assert _order(platform, conn, limit="780").status is OrderStatus.OPEN  # references kept after rotation


def test_the_same_stock_from_two_brokers_is_one_instrument():
    kite_platform, kite_manager, kite_conn, kite = _setup("KITE")
    dhan = FakeDhan(T0)
    kite_manager._http_factory = lambda v: dhan.client() if v == "DHAN" else kite.client()
    dhan_conn = kite_manager.create(
        name="Dhan",
        venue="DHAN",
        environment=Environment.PRODUCTION,
        actor="t",
        api_key=dhan.client_id,
        api_secret=dhan.token,
        settings={"product": "CNC"},
    )
    assert len([i for i in kite_platform.instruments.all() if i.instrument_id == SBIN]) == 1
    first = _order(kite_platform, kite_conn, limit="781")
    second = _order(kite_platform, dhan_conn, limit="782")
    assert first.status is OrderStatus.OPEN and second.status is OrderStatus.OPEN
    assert any(o["symbol"] == "SBIN" for o in kite.orders.values())  # Kite got its trading symbol
    assert any(o["symbol"] == "3045" for o in dhan.orders.values())  # Dhan got its security id


# ---- Delta Exchange India ---------------------------------------------------------------------------


def _delta(secret=None):
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    fake = FakeDelta(T0)
    manager = ConnectionManager(platform, platform.store, _secrets(platform.store), lambda v: fake.client())
    conn = manager.create(
        name="Delta testnet",
        venue="DELTA",
        environment=Environment.TESTNET,
        actor="t",
        api_key=fake.key,
        api_secret=secret or fake.secret,
        base_currency="USD",
    )
    manager.set_watchlist(conn.connection_id, ["DELTA:BTCUSD"])
    _poll(platform, manager)
    return platform, manager, conn, fake


def test_delta_loads_perpetuals_and_signs_requests():
    from jdquant.autopilot.research import market_leverage
    from jdquant.markets.sessions import CRYPTO_24_7, session_for

    platform, manager, conn, fake = _delta()
    assert conn.instrument_count == 2 and conn.status is ConnectionStatus.CONNECTED
    btc = platform.instruments.get("DELTA:BTCUSD")
    assert btc.asset_class is AssetClass.CRYPTO_PERPETUAL and btc.can_short
    assert btc.contract_multiplier == Decimal("0.001") and btc.tick_size == Decimal("0.5")
    assert session_for(btc) is CRYPTO_24_7 and market_leverage(btc, False)[0] == 10
    assert manager.adapter_for_account(conn.account_id).fetch_balances()[0].free == Decimal(900)
    with pytest.raises(PlatformError, match="CONNECTION_TEST_FAILED"):
        _delta(secret="wrong")


def test_delta_orders_short_fill_and_cancel():
    platform, manager, conn, fake = _delta()
    short = _order(platform, conn, side=Side.SELL, qty=5, instrument="DELTA:BTCUSD")
    assert short.status is OrderStatus.FILLED
    fill = platform.oms.fills[-1]
    assert fill.price == fake.bid and fill.fee_asset == "USD"
    assert fill.fee == (
        Decimal(5) * fake.bid * Decimal("0.001") * Decimal("0.0005") * Decimal("1.18")
    ).quantize(Decimal("0.00000001"))
    held = {p.instrument_id: p.quantity for p in platform.positions.positions()}
    assert held["DELTA:BTCUSD"] == Decimal(-5)
    resting = _order(platform, conn, qty=2, limit="59000", instrument="DELTA:BTCUSD")
    assert resting.status is OrderStatus.OPEN
    fake.fill(resting.client_order_id.replace("-", "")[-32:])
    _poll(platform, manager)
    assert resting.status is OrderStatus.FILLED and platform.oms.fills[-1].price == Decimal(59000)
    doomed = _order(platform, conn, qty=1, limit="58000", instrument="DELTA:BTCUSD")
    platform.oms.cancel(doomed.order_id)
    assert doomed.status is OrderStatus.CANCELED


def test_delta_candles():
    platform, manager, _, _ = _delta()
    adapter = manager.data_source_for("DELTA:BTCUSD")
    candles = adapter.fetch_candles(platform.instruments.get("DELTA:BTCUSD"), 300, 50)
    assert len(candles) == 50 and all(c.close_ts <= T0 for c in candles)
