"""Crypto exchanges through CCXT: the real CCXT exchange classes with their network calls replaced."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import ccxt
import pytest
from conftest import T0

from jdquant.connectivity.base import Environment, VenueError
from jdquant.connectivity.ccxt_adapter import CcxtAdapter, exchanges
from jdquant.connectivity.connections import markets_of
from jdquant.core.clock import SimulatedClock
from jdquant.core.types import Side
from jdquant.oms.orders import OrderStatus, OrderType, ReportType

MARKETS = {
    "BTC/INR": {
        "id": "btc-inr",
        "symbol": "BTC/INR",
        "base": "BTC",
        "quote": "INR",
        "type": "spot",
        "spot": True,
        "active": True,
        "precision": {"amount": 0.0001, "price": 1},
        "limits": {"amount": {"min": 0.0001, "max": 10}, "cost": {"min": 100}},
    },
    "ETH/USDT": {
        "id": "eth-usdt",
        "symbol": "ETH/USDT",
        "base": "ETH",
        "quote": "USDT",
        "type": "spot",
        "spot": True,
        "active": True,
        "precision": {"amount": 0.001, "price": 0.01},
        "limits": {"amount": {"min": 0.001}},
    },
    "BTC/USDT:USDT": {
        "id": "BTCUSDT-PERP",
        "symbol": "BTC/USDT:USDT",
        "base": "BTC",
        "quote": "USDT",
        "type": "swap",
        "swap": True,
        "linear": True,
        "active": True,
        "contractSize": 0.001,
        "precision": {"amount": 1, "price": 0.1},
        "limits": {"amount": {"min": 1}},
    },
}


def _client():
    client = ccxt.zebpay()
    calls = []
    client.load_markets = lambda *a, **k: MARKETS
    client.fetch_ticker = lambda symbol: {
        "bid": 5_000_000,
        "ask": 5_001_000,
        "bidVolume": 0.5,
        "askVolume": 0.3,
        "timestamp": int(T0.timestamp() * 1000),
    }
    client.fetch_order_book = lambda symbol, limit: {
        "bids": [[5_000_000, 0.5], [4_999_000, 1.2]],
        "asks": [[5_001_000, 0.3]],
        "timestamp": None,
    }
    start = int((T0 - timedelta(hours=3)).timestamp() * 1000)
    client.fetch_ohlcv = lambda symbol, tf, since, limit: [
        [start + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 10] for i in range(4)
    ]
    client.fetch_balance = lambda: {"free": {"INR": 250000.0, "BTC": 0.02}, "used": {"INR": 1000.0}}

    def create_order(symbol, kind, side, amount, price, params):
        calls.append(("create", symbol, kind, side, amount, price, params))
        return {
            "id": "Z1",
            "status": "open",
            "filled": 0.001,
            "average": 5_001_000,
            "timestamp": None,
            "fee": {"cost": 5.0, "currency": "INR"},
        }

    client.create_order = create_order
    client.fetch_order = lambda order_id, symbol: {
        "id": order_id,
        "status": "closed",
        "filled": 0.002,
        "average": 5_002_000,
        "timestamp": None,
        "fee": {"cost": 12.0, "currency": "INR"},
    }
    client.cancel_order = lambda order_id, symbol: calls.append(("cancel", order_id, symbol))
    return client, calls


def _adapter(**settings):
    client, calls = _client()
    adapter = CcxtAdapter(
        SimulatedClock(T0),
        api_key="k",
        api_secret="s",
        exchange_id="zebpay",
        client=client,
        settings=settings,
    )
    reports = []
    adapter.set_report_handler(reports.append)
    return adapter, client, calls, reports


def test_markets_quotes_depth_candles_and_balances():
    adapter, *_ = _adapter(quotes=["INR"])
    [btc] = adapter.fetch_instruments()
    assert btc.instrument_id == "ZEBPAY:BTC-INR" and btc.quote_asset == "INR"
    assert btc.tick_size == Decimal(1) and btc.lot_size == Decimal("0.0001") and btc.min_notional == 100
    assert markets_of("CCXT_ZEBPAY") == ("ZEBPAY",)
    quote = adapter.fetch_quote(btc)
    assert quote.bid_price == Decimal(5_000_000) and quote.ask_price == Decimal(5_001_000)
    book = adapter.fetch_depth(btc)
    assert book.source == "ZEBPAY" and book.bids[1].quantity == 1.2
    candles = adapter.fetch_candles(btc, 3600, 10)
    assert len(candles) == 3  # the bar still forming at T0 is left out
    assert {b.asset: b.free for b in adapter.fetch_balances()} == {
        "BTC": Decimal("0.02"),
        "INR": Decimal(250000),
    }
    with pytest.raises(VenueError, match="no 180s"):
        adapter.fetch_candles(btc, 180, 10)  # ZebPay offers no 3-minute bars


def test_swaps_can_be_included_and_shorted():
    adapter, *_ = _adapter(market_type="both")
    by_id = {i.instrument_id: i for i in adapter.fetch_instruments()}
    perp = by_id["ZEBPAY:BTCUSDT-PERP"]
    assert perp.can_short and perp.contract_multiplier == Decimal("0.001")
    assert not by_id["ZEBPAY:ETH-USDT"].can_short


def test_orders_fill_from_cumulative_reports_and_errors_map():
    adapter, client, calls, reports = _adapter()
    adapter.fetch_instruments()
    order = SimpleNamespace(
        client_order_id="JQ-1",
        instrument_id="ZEBPAY:BTC-INR",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.002"),
        limit_price=Decimal(5_002_000),
        post_only=False,
        venue_order_id=None,
        status=OrderStatus.SUBMITTED,
        filled_quantity=Decimal(0),
        average_fill_price=None,
    )
    adapter.submit(order)
    assert calls[0][:4] == ("create", "BTC/INR", "limit", "buy") and calls[0][6]["clientOrderId"] == "JQ-1"
    ack, fill = reports
    assert ack.report_type is ReportType.ACK and ack.venue_order_id == "Z1"
    assert fill.quantity == Decimal("0.001") and fill.price == Decimal(5_001_000) and fill.fee == 5
    # the order now holds 0.001 at 5,001,000; the exchange reports 0.002 at an average of 5,002,000
    order.venue_order_id, order.status = "Z1", OrderStatus.PARTIALLY_FILLED
    order.filled_quantity, order.average_fill_price = Decimal("0.001"), Decimal(5_001_000)
    reports.clear()
    adapter.poll([order])
    [second] = reports
    assert second.quantity == Decimal("0.001") and second.price == Decimal(5_003_000) and second.fee == 7

    def broke(*a):
        raise ccxt.InsufficientFunds("zebpay: not enough INR")

    client.create_order = broke
    reports.clear()
    adapter.submit(order)
    assert reports[0].report_type is ReportType.REJECT and reports[0].reason == "INSUFFICIENT_BALANCE"
    adapter.cancel(order)
    assert calls[-1] == ("cancel", "Z1", "BTC/INR")


def test_native_exchanges_and_missing_testnets_are_refused():
    with pytest.raises(VenueError, match="BINANCE directly"):
        CcxtAdapter(SimulatedClock(T0), exchange_id="binance")
    with pytest.raises(VenueError) as err:
        CcxtAdapter(SimulatedClock(T0), exchange_id="bitbns", environment=Environment.TESTNET)
    assert err.value.code == "TESTNET_UNAVAILABLE"
    listed = exchanges()
    assert [e["id"] for e in listed[:3]] == ["bitbns", "mudrex", "zebpay"]  # Indian exchanges first
    assert not any(e["id"] == "binance" for e in listed)


def test_connect_through_the_api(app_ctx, monkeypatch):
    client, c, platform, _ = app_ctx
    monkeypatch.setattr(ccxt.zebpay, "load_markets", lambda self, *a, **k: MARKETS)
    listed = client.get("/api/v1/connections/ccxt-exchanges").json()
    assert listed["available"] and listed["exchanges"][0]["indian"]
    created = client.post(
        "/api/v1/connections",
        json={
            "name": "ZebPay",
            "venue": "CCXT_ZEBPAY",
            "environment": "PRODUCTION",
            "base_currency": "INR",
            "settings": {"quotes": ["INR"]},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["markets"] == ["ZEBPAY"] and body["instrument_count"] == 1
    assert platform.instruments.get("ZEBPAY:BTC-INR").aliases == (("CCXT", "BTC/INR"),)
    refused = client.post(
        "/api/v1/connections", json={"name": "B", "venue": "CCXT_BINANCE", "environment": "PRODUCTION"}
    )
    assert refused.status_code == 400
