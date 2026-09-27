"""OANDA (spot gold and forex): connection, instruments, prices, orders, sessions and costs."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import T0
from fake_venues import FakeOanda

from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ConnectionManager, ConnectionStatus
from jdquant.connectivity.poller import VenuePoller
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.marketdata.records import Quote
from jdquant.markets.forex import FX_FEES, financing_rate, rollover_days
from jdquant.markets.sessions import FOREX, SPOT_METALS, asset_group, session_for
from jdquant.oms.orders import Liquidity, OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.secrets import SecretBox, SecretStore
from jdquant.strategy.runner import DeploymentRunner


def _setup(fake=None, token=None):
    from cryptography.fernet import Fernet

    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    fake = fake or FakeOanda()
    fake.now = T0
    secrets = SecretStore(platform.store, SecretBox(Fernet.generate_key()))
    manager = ConnectionManager(platform, platform.store, secrets, lambda v: fake.client())
    conn = manager.create(
        name="OANDA practice",
        venue="OANDA",
        environment=Environment.TESTNET,
        actor="t",
        api_key=token or fake.token,
        api_secret=fake.account,
        base_currency="INR",  # replaced by the account's own currency
    )
    manager.set_watchlist(conn.connection_id, ["OANDA:XAU_USD", "OANDA:EUR_USD", "OANDA:USD_JPY"])
    _poll(platform, manager)  # prices first: the risk checks need them
    return platform, manager, conn, fake


def _poll(platform, manager):
    VenuePoller(platform, manager, DeploymentRunner(platform, manager.data_source_for)).tick()


def _order(platform, conn, symbol, side, qty, limit=None):
    return platform.oms.submit(
        OrderRequest(
            conn.account_id,
            f"OANDA:{symbol}",
            side,
            OrderType.LIMIT if limit else OrderType.MARKET,
            Decimal(qty),
            limit_price=Decimal(limit) if limit else None,
        )
    )


def test_practice_connection_loads_gold_and_forex():
    platform, manager, conn, fake = _setup()
    assert conn.status is ConnectionStatus.CONNECTED and conn.instrument_count == 3
    assert fake.requests[0].url.host == "api-fxpractice.oanda.com"
    account = platform.trading.get_account(conn.account_id)
    assert account.mode.value == "LIVE" and account.base_currency == "USD"
    gold = platform.instruments.get("OANDA:XAU_USD")
    assert gold.asset_class is AssetClass.COMMODITY and gold.can_short
    assert gold.tick_size == Decimal("0.01") and gold.max_quantity == Decimal(5000)
    assert platform.instruments.get("OANDA:EUR_USD").tick_size == Decimal("0.00001")
    assert asset_group(gold) == "Gold · XAU/USD"
    assert asset_group(platform.instruments.get("OANDA:USD_JPY")) == "Forex"
    balance = manager.adapter_for_account(conn.account_id).fetch_balances()[0]
    assert balance.asset == "USD" and balance.free == Decimal(98000)


def test_bad_token_is_refused():
    with pytest.raises(PlatformError) as err:
        _setup(token="wrong")
    assert err.value.code == "CONNECTION_TEST_FAILED"


def test_market_orders_fill_at_bid_and_ask_and_can_go_short():
    platform, manager, conn, fake = _setup()
    buy = _order(platform, conn, "XAU_USD", Side.BUY, 2)
    assert buy.status is OrderStatus.FILLED and platform.oms.fills[-1].price == Decimal("4000.20")
    body = json.loads(fake.requests[-1].content)["order"]
    assert body == {
        "instrument": "XAU_USD",
        "units": "2",
        "positionFill": "DEFAULT",
        "clientExtensions": {"id": buy.client_order_id, "tag": "jdquant"},
        "type": "MARKET",
        "timeInForce": "FOK",
    }
    short = _order(platform, conn, "EUR_USD", Side.SELL, 1000)
    assert short.status is OrderStatus.FILLED
    assert json.loads(fake.requests[-1].content)["order"]["units"] == "-1000"
    assert platform.oms.fills[-1].price == Decimal("1.16993")
    held = {p.instrument_id: p.quantity for p in platform.positions.positions()}
    assert held["OANDA:EUR_USD"] == Decimal(-1000) and held["OANDA:XAU_USD"] == Decimal(2)


def test_unfillable_market_order_expires_and_unknown_instrument_is_rejected():
    platform, _, conn, fake = _setup()
    fake.fill_market = False
    order = _order(platform, conn, "EUR_USD", Side.BUY, 1000)
    assert order.status is OrderStatus.EXPIRED
    platform.instruments.add(
        Instrument(
            "OANDA", "GBP_NZD", AssetClass.FX, "GBP", "NZD", Decimal("0.00001"), Decimal(1), Decimal(1)
        )
    )
    platform.market.on_quote(  # the risk checks need a price
        Quote("OANDA:GBP_NZD", T0, Decimal("2.2"), Decimal(1), Decimal("2.2001"), Decimal(1))
    )
    rejected = _order(platform, conn, "GBP_NZD", Side.BUY, 100)
    assert rejected.status is OrderStatus.REJECTED and rejected.reject_code == "INSTRUMENT_UNKNOWN"


def test_limit_order_rests_then_fills_or_cancels():
    platform, manager, conn, fake = _setup()
    resting = _order(platform, conn, "EUR_USD", Side.BUY, 1000, limit="1.16000")
    assert resting.status is OrderStatus.OPEN
    fake.fill_limit(resting.client_order_id)
    _poll(platform, manager)
    assert resting.status is OrderStatus.FILLED
    assert platform.oms.fills[-1].price == Decimal("1.16000")
    second = _order(platform, conn, "EUR_USD", Side.BUY, 1000, limit="1.15000")
    platform.oms.cancel(second.order_id)
    assert second.status is OrderStatus.CANCELED
    assert fake.requests[-1].url.path.endswith(f"/orders/@{second.client_order_id}/cancel")


def test_candles_are_mid_prices_complete_only_and_paged():
    platform, manager, _, fake = _setup()
    adapter = manager.data_source_for("OANDA:XAU_USD")
    gold = platform.instruments.get("OANDA:XAU_USD")
    candles = adapter.fetch_candles(gold, 900, 30)
    assert len(candles) == 30 and all(c.close_ts <= T0 for c in candles)
    assert candles[-1].close_ts == T0 and candles[1].open_ts - candles[0].open_ts == timedelta(minutes=15)
    fake.requests.clear()
    many = adapter.fetch_candles(gold, 60, 6000)
    assert len(many) == 6000 and len(fake.requests) == 2  # 5000 per request, paged backwards
    assert many[0].open_ts < many[-1].open_ts and len({c.open_ts for c in many}) == 6000


def test_quotes_come_from_pricing():
    platform, manager, conn, fake = _setup()
    assert platform.market.reference_price("OANDA:XAU_USD") == Decimal("4000.00")
    assert fake.requests[-1].url.params["instruments"] in ("XAU_USD", "EUR_USD", "USD_JPY")


def test_forex_sessions():
    sunday = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)  # 16:00 New York: still closed
    assert not FOREX.is_open(sunday) and FOREX.is_open(sunday + timedelta(minutes=70))
    monday_1650 = datetime(2026, 9, 28, 20, 50, tzinfo=UTC)
    assert FOREX.is_open(monday_1650) and FOREX.past_intraday_cutoff(monday_1650)
    after_rollover = datetime(2026, 9, 28, 21, 30, tzinfo=UTC)  # 17:30 New York
    assert FOREX.is_open(after_rollover) and not SPOT_METALS.is_open(after_rollover)  # gold's hour off
    friday_close = datetime(2026, 10, 2, 21, 10, tzinfo=UTC)
    assert not FOREX.is_open(friday_close)
    assert FOREX.next_open(friday_close) == datetime(2026, 10, 4, 21, 5, tzinfo=UTC)
    platform, *_ = _setup()
    assert session_for(platform.instruments.get("OANDA:XAU_USD")) is SPOT_METALS
    assert FOREX.bars_per_year(86400) == 260


def test_spread_and_financing_costs():
    platform, *_ = _setup()
    gold = platform.instruments.get("OANDA:XAU_USD")
    eur = platform.instruments.get("OANDA:EUR_USD")
    # Half of a 40-cent spread per ounce on each fill priced at the mid.
    assert FX_FEES.fee(gold, Side.BUY, Decimal(10), Decimal(4000), Liquidity.TAKER) == Decimal("2.00")
    assert FX_FEES.fee(eur, Side.SELL, Decimal(10000), Decimal("1.17"), Liquidity.TAKER) == Decimal("0.70")
    # Long gold pays the dollar rate plus the markup; short gold's credit is not counted.
    assert financing_rate(gold, 1) == Decimal("0.065") and financing_rate(gold, -1) == Decimal(0)
    tuesday = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)  # 16:00 New York
    assert rollover_days(tuesday, tuesday + timedelta(hours=2)) == 1
    assert rollover_days(tuesday + timedelta(days=1), tuesday + timedelta(days=1, hours=2)) == 3  # Wednesday
    assert rollover_days(tuesday - timedelta(hours=4), tuesday) == 0


def test_backtest_charges_overnight_financing():
    from jdquant.backtest.engine import BacktestConfig, run_backtest
    from jdquant.marketdata.synthetic import random_walk_candles
    from jdquant.strategy.base import Strategy

    class BuyAndHold(Strategy):
        name = "hold"

        def on_bar(self, candle):
            if self.ctx.position(candle.instrument_id) == 0:
                self.ctx.buy(candle.instrument_id, Decimal(1))

    platform, *_ = _setup()
    gold = platform.instruments.get("OANDA:XAU_USD")
    start = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)  # Monday
    candles = random_walk_candles(gold, start, 7 * 24, start_price=Decimal(4000), volatility=0.0)
    result = run_backtest(
        BacktestConfig(
            BuyAndHold, [gold], {gold.instrument_id: candles}, initial_capital=Decimal(10000), fees=FX_FEES
        )
    )
    # Held Monday to Sunday: rollovers Mon, Tue, Wed (x3), Thu, Fri = 7 days at 6.5% a year on ~4000.
    assert result.financing == pytest.approx(Decimal(4000) * Decimal("0.065") * 7 / 365, rel=Decimal("0.01"))
    assert result.final_equity < Decimal(10000) - result.financing
