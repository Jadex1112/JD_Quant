"""Adapter conformance suite (FR-45002) run against simulated Binance and Alpaca servers."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import T0, advance
from fake_venues import FakeAlpaca, FakeBinance

from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ConnectionManager
from jdquant.connectivity.poller import VenuePoller
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.oms.orders import OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.secrets import SecretBox, SecretStore
from jdquant.strategy.runner import DeploymentRunner

S = OrderStatus


@dataclass
class Harness:
    name: str
    fake: object
    platform: object
    manager: ConnectionManager
    account_id: str
    instrument_id: str
    qty: str
    resting_price: str
    poller: VenuePoller

    def order(self, side=Side.BUY, qty=None, price=None, order_type=OrderType.LIMIT):
        limit_price = Decimal(price or self.resting_price) if order_type is OrderType.LIMIT else None
        return self.platform.oms.submit(
            OrderRequest(
                self.account_id,
                self.instrument_id,
                side,
                order_type,
                Decimal(qty or self.qty),
                limit_price=limit_price,
            )
        )

    def venue_fill(self, order, qty, price):
        if self.name == "binance":
            self.fake.trade("BTCUSDT", price, qty)
        else:
            self.fake.fill(order.venue_order_id, qty, price)


def _harness(name: str) -> Harness:
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    if name == "binance":
        fake = FakeBinance(now_ms=int(T0.timestamp() * 1000))
        creds, venue, env, inst, qty, price = (
            ("k", "s"),
            "BINANCE",
            Environment.TESTNET,
            "BINANCE:BTCUSDT",
            "0.1",
            "49000",
        )
    else:
        fake = FakeAlpaca()
        creds, venue, env, inst, qty, price = (
            ("ak", "as"),
            "ALPACA",
            Environment.TESTNET,
            "ALPACA:AAPL",
            "10",
            "195",
        )
    secrets = SecretStore(platform.store, _box())
    manager = ConnectionManager(platform, platform.store, secrets, lambda v: fake.client())
    conn = manager.create(
        name=name, venue=venue, environment=env, actor="t", api_key=creds[0], api_secret=creds[1]
    )
    manager.set_watchlist(conn.connection_id, [inst])
    runner = DeploymentRunner(platform, manager.data_source_for)
    poller = VenuePoller(platform, manager, runner)
    poller.tick()  # first price refresh
    return Harness(name, fake, platform, manager, conn.account_id, inst, qty, price, poller)


def _box():
    from cryptography.fernet import Fernet

    return SecretBox(Fernet.generate_key())


@pytest.fixture(params=["binance", "alpaca"])
def venue(request) -> Harness:
    return _harness(request.param)


def test_connection_syncs_instruments_and_prices(venue):
    instrument = venue.platform.instruments.get(venue.instrument_id)
    assert instrument.tick_size == Decimal("0.01")
    assert venue.platform.market.reference_price(venue.instrument_id) is not None
    assert venue.platform.trading.get_account(venue.account_id).mode.value == "LIVE"
    assert venue.manager.adapter_for_account(venue.account_id).fetch_balances()


def test_limit_order_lifecycle_with_partial_fill_and_cancel(venue):
    order = venue.order()
    assert order.status is S.OPEN and order.venue_order_id
    half = str(Decimal(venue.qty) / 2)
    venue.venue_fill(order, half, venue.resting_price)
    venue.poller.tick()
    venue.poller.tick()  # a second poll must not duplicate the fill
    assert order.status is S.PARTIALLY_FILLED and order.filled_quantity == Decimal(half)
    assert venue.platform.positions.net_quantity(venue.account_id, venue.instrument_id) == Decimal(half)
    venue.platform.oms.cancel(order.order_id)
    venue.poller.tick()
    assert order.status is S.CANCELED


def test_market_order_fills_immediately(venue):
    order = venue.order(order_type=OrderType.MARKET)
    venue.poller.tick()
    assert order.status is S.FILLED
    assert order.average_fill_price > 0


def test_venue_rejection_is_normalized(venue):
    order = venue.order(qty="1000" if venue.name == "alpaca" else "3")
    assert order.status is S.REJECTED and order.reject_code == "INSUFFICIENT_BALANCE"


def test_timeout_never_resubmits_and_is_resolved_by_query(venue):
    """AC-21003 against a real protocol: a lost request becomes UNKNOWN, then REJECTED."""
    venue.fake.fail_next = "timeout"
    order = venue.order()
    assert order.status is S.SUBMITTED
    advance(venue.platform.clock, seconds=6)
    venue.poller.tick()
    assert order.status is S.REJECTED and order.reject_code == "NOT_RECEIVED"


def test_native_modify_moves_the_order(venue):
    order = venue.order()
    old_client_id = order.client_order_id
    new_price = str(Decimal(venue.resting_price) - 1)
    venue.platform.oms.modify(order.order_id, limit_price=Decimal(new_price))
    assert order.status is S.OPEN and order.limit_price == Decimal(new_price)
    assert order.client_order_id != old_client_id
    venue.venue_fill(order, venue.qty, new_price)
    venue.poller.tick()
    assert order.status is S.FILLED


def test_keys_with_withdrawal_permission_are_refused():
    """CON-082, FR-19003."""
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    fake = FakeBinance(can_withdraw=True, now_ms=int(T0.timestamp() * 1000))
    manager = ConnectionManager(
        platform, platform.store, SecretStore(platform.store, _box()), lambda v: fake.client()
    )
    with pytest.raises(PlatformError) as err:
        manager.create(
            name="x", venue="BINANCE", environment=Environment.TESTNET, actor="t", api_key="k", api_secret="s"
        )
    assert err.value.code == "WITHDRAWAL_PERMISSION_REFUSED"
    assert not platform.store.query("SELECT 1 FROM secrets")


def test_wrong_credentials_fail_the_connection_test():
    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    fake = FakeBinance(now_ms=int(T0.timestamp() * 1000))
    manager = ConnectionManager(
        platform, platform.store, SecretStore(platform.store, _box()), lambda v: fake.client()
    )
    with pytest.raises(PlatformError) as err:
        manager.create(
            name="x",
            venue="BINANCE",
            environment=Environment.TESTNET,
            actor="t",
            api_key="k",
            api_secret="wrong",
        )
    assert err.value.code == "CONNECTION_TEST_FAILED"


def test_credentials_are_encrypted_and_connections_reload_offline(tmp_path):
    clock = SimulatedClock(T0)
    db = tmp_path / "db.sqlite"
    box = _box()
    fake = FakeBinance(now_ms=int(T0.timestamp() * 1000), secret="super-secret-value")
    platform = build_paper_platform(clock, store=Store(db))
    manager = ConnectionManager(
        platform, platform.store, SecretStore(platform.store, box), lambda v: fake.client()
    )
    conn = manager.create(
        name="b",
        venue="BINANCE",
        environment=Environment.TESTNET,
        actor="t",
        api_key="k",
        api_secret="super-secret-value",
    )
    platform.store.close()
    assert b"super-secret-value" not in db.read_bytes()

    def offline(_venue):
        import httpx

        def refuse(request):
            raise httpx.ConnectError("offline", request=request)

        return httpx.Client(transport=httpx.MockTransport(refuse))

    holder = {}

    def attach(p):
        m = ConnectionManager(p, p.store, SecretStore(p.store, box), offline)
        m.load_all()
        holder["m"] = m

    restarted = build_paper_platform(clock, store=Store(db), before_recovery=attach)
    assert restarted.trading.get_account(conn.account_id)
    assert restarted.instruments.get("BINANCE:BTCUSDT").min_notional == Decimal(5)
    assert holder["m"].adapter_for_account(conn.account_id) is not None


def test_connection_api_hides_credentials(platform):
    from conftest import OWNER
    from fastapi.testclient import TestClient

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    fake = FakeBinance(now_ms=int(T0.timestamp() * 1000))
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
        json={
            "name": "Binance test",
            "venue": "BINANCE",
            "environment": "TESTNET",
            "api_key": "k",
            "api_secret": "s",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "CONNECTED" and body["account_id"]
    listing = client.get("/api/v1/connections").text
    assert '"api_secret"' not in listing and '"api_key"' not in listing
    balances = client.get(f"/api/v1/accounts/{body['account_id']}/balances").json()
    assert {b["asset"] for b in balances} >= {"USDT"}


def test_strategy_runner_trades_on_quote_built_bars(platform):
    """A deployment on the paper account turns injected quotes into bars and trades them."""
    from conftest import BTC, set_quote

    from jdquant.marketdata.records import Quote

    manager = ConnectionManager(
        platform,
        platform.store or Store(":memory:"),
        SecretStore(platform.store or Store(":memory:"), _box()),
    )
    runner = DeploymentRunner(platform, manager.data_source_for)
    poller = VenuePoller(platform, manager, runner)
    dep = platform.trading.create_deployment(
        strategy_name="ma_crossover",
        strategy_version="1.0.0",
        account_id="paper-main",
        parameters={"fast": 2, "slow": 3, "quantity": "0.01"},
        instruments=[BTC],
        created_by="u",
        bar_interval_seconds=60,
    )
    platform.trading.approve(dep.deployment_id, "u")
    platform.trading.start(dep.deployment_id)
    runner.sync(dep)
    price = Decimal(50000)
    for minute in range(8):
        platform.clock.set(T0 + timedelta(minutes=minute, seconds=30))
        price += 100
        set_quote(platform, BTC, str(price - 1), str(price + 1))
        poller.on_quote(Quote(BTC, platform.clock.now(), price - 1, Decimal(1), price + 1, Decimal(1)))
    orders = platform.oms.list_orders(deployment_id=dep.deployment_id)
    assert orders and all(o.status is S.FILLED for o in orders)
    assert platform.positions.net_quantity("paper-main", BTC) == Decimal("0.01")

    platform.trading.stop(dep.deployment_id)
    runner.sync(dep)
    assert dep.deployment_id not in runner.hosted
