"""Order flow: delta, aggression, large trades, sweeps, absorption, icebergs, spikes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from jdquant.core.clock import SimulatedClock
from jdquant.intelligence.events import EventEngine, EventStore
from jdquant.intelligence.orderflow import OrderFlowEngine
from jdquant.marketdata.book import BookSnapshot, Level
from jdquant.marketdata.hub import MarketDataHub
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.marketdata.recorder import MarketStore

T0 = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)  # 09:30 IST
SBIN = Instrument("NSE", "SBIN-EQ", AssetClass.EQUITY, "SBIN", "INR", Decimal("0.05"), Decimal(1), Decimal(1))


class Feed:
    """Publishes a 5-level book around a mid price with a running day volume."""

    def __init__(self):
        clock = SimulatedClock(T0)
        self.events = EventEngine(clock, EventStore(MarketStore(":memory:")))
        self.hub = MarketDataHub(clock)
        self.flow = OrderFlowEngine(self.events.emit, lambda iid: SBIN)
        self.hub.book_listeners.append(self.flow.on_book)
        self.hub.tick_listeners.append(self.flow.on_tick)
        self.volume = 0.0
        self.t = 0.0

    def book(self, bid=800.00, ask=800.05, ltp=None, traded=0.0, bid_qty=100.0, ask_qty=100.0, dt=1.0):
        self.t += dt
        self.volume += traded
        at = T0 + timedelta(seconds=self.t)
        step = 0.05
        snapshot = BookSnapshot(
            SBIN.instrument_id,
            "DHAN",
            at,
            at,
            bids=tuple(Level(round(bid - k * step, 2), bid_qty if k == 0 else 100.0, 3) for k in range(5)),
            asks=tuple(Level(round(ask + k * step, 2), ask_qty if k == 0 else 100.0, 3) for k in range(5)),
            last_price=ltp if ltp is not None else bid,
            volume=self.volume,
        )
        self.hub.publish(snapshot)

    def buy(self, qty, **kw):
        kw.setdefault("ltp", kw.get("ask", 800.05))
        self.book(traded=qty, **kw)

    def sell(self, qty, **kw):
        kw.setdefault("ltp", kw.get("bid", 800.00))
        self.book(traded=qty, **kw)

    def balanced_minutes(self, minutes, qty=10.0):
        for _ in range(minutes):
            for k in range(6):
                (self.buy if k % 2 else self.sell)(qty, dt=10.0)

    def kinds(self):
        return [e.kind for e in self.events.recent(SBIN.instrument_id, 500)]


def test_delta_and_cumulative_delta():
    f = Feed()
    f.book()
    f.buy(30)
    f.buy(20)
    f.sell(15)
    snap = f.flow.snapshot(SBIN.instrument_id)
    assert snap["cvd"] == pytest.approx(35) and snap["day_volume"] == pytest.approx(65)
    assert snap["bars"][-1]["buy"] == 50 and snap["bars"][-1]["sell"] == 15
    assert snap["estimated"] is True


def test_aggressive_buying_needs_real_volume_and_one_sided_flow():
    f = Feed()
    f.book()
    f.balanced_minutes(10)
    assert "AGGRESSIVE_BUYING" not in f.kinds()
    for _ in range(6):
        f.buy(40, dt=5.0)
    assert "AGGRESSIVE_BUYING" in f.kinds()


def test_large_trades_and_cluster():
    f = Feed()
    f.book()
    for k in range(40):
        (f.buy if k % 2 else f.sell)(10, dt=1.0)
    for _ in range(3):
        f.buy(200, dt=2.0)
    kinds = f.kinds()
    assert kinds.count("LARGE_TRADE") == 3
    cluster = next(e for e in f.events.recent(SBIN.instrument_id) if e.kind == "LARGE_TRADE_CLUSTER")
    assert cluster.data["count"] == 3 and cluster.data["quantity"] == 600


def test_buy_sweep_through_several_levels():
    f = Feed()
    f.book()
    f.book(bid=800.15, ask=800.25, ltp=800.15, traded=450)  # bought through 800.05 ... 800.15 (+ more)
    sweep = next(e for e in f.events.recent(SBIN.instrument_id) if e.kind == "SWEEP")
    assert sweep.data["side"] == "BUY" and sweep.data["levels"] >= 3


def test_absorption_selling_into_a_holding_bid():
    f = Feed()
    f.book()
    f.balanced_minutes(5)
    for _ in range(6):
        f.sell(40, dt=4.0)  # heavy selling at 800.00, price does not fall
    absorbed = next(e for e in f.events.recent(SBIN.instrument_id) if e.kind == "ABSORPTION")
    assert absorbed.data["side"] == "BID"


def test_iceberg_like_refilling_bid():
    f = Feed()
    f.book(bid_qty=50)
    for k in range(40):
        (f.buy if k % 2 else f.sell)(2, bid_qty=50, dt=1.0)
    for _ in range(10):
        f.sell(20, bid_qty=50, dt=1.0)  # 200 traded at 800.00; the bid keeps showing 50
    ice = next(e for e in f.events.recent(SBIN.instrument_id) if e.kind == "ICEBERG_LIKE")
    assert ice.data["side"] == "BID" and ice.data["executed"] >= 2 * ice.data["max_displayed"]


def test_volume_spike_on_bar_close():
    f = Feed()
    f.book()
    f.balanced_minutes(8)
    for _ in range(6):
        f.buy(100, dt=10.0)  # a busy minute
    f.buy(1, dt=10.0)  # next minute closes the busy one
    assert "VOLUME_SPIKE" in f.kinds()
