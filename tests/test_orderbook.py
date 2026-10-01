"""Order-book intelligence: walls, withdrawal vs consumption, migration, out-of-view and cross-feed."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from jdquant.core.clock import SimulatedClock
from jdquant.intelligence.events import EventEngine, EventStore
from jdquant.intelligence.orderbook import OrderBookEngine, expected_fill
from jdquant.marketdata.book import BUY, SELL, BookSnapshot, Level, infer_tick
from jdquant.marketdata.hub import MarketDataHub
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.marketdata.recorder import MarketStore

T0 = datetime(2026, 9, 28, 5, 32, 1, tzinfo=UTC)  # 11:02:01 IST
CUPID = Instrument(
    "NSE", "CUPID-EQ", AssetClass.EQUITY, "CUPID", "INR", Decimal("0.05"), Decimal(1), Decimal(1)
)


def book(t, bids, asks, *, source="DHAN", ltp=None, volume=None, capacity=5):
    at = T0 + timedelta(seconds=t)
    return BookSnapshot(
        CUPID.instrument_id,
        source,
        at,
        at,
        bids=tuple(Level(p, q, o) for p, q, o in bids),
        asks=tuple(Level(p, q, o) for p, q, o in asks),
        last_price=ltp,
        volume=volume,
        capacity=capacity,
    )


ASKS = [(263.25, 3980, 12), (263.30, 958, 5), (263.35, 2374, 9), (263.40, 3100, 10), (263.45, 2800, 8)]


def bids_with_wall(wall_qty):
    return [
        (263.20, 3000, 10),
        (263.15, 2500, 9),
        (263.10, 4000, 11),
        (263.05, 3500, 12),
        (263.00, wall_qty, 184),
    ]


@pytest.fixture
def setup(tmp_path):
    clock = SimulatedClock(T0)
    events = EventEngine(clock, EventStore(MarketStore(":memory:")))
    hub = MarketDataHub(clock)
    engine = OrderBookEngine(events.emit, lambda iid: CUPID, other_books=hub.books)
    hub.book_listeners.append(engine.on_book)
    return hub, engine, events


def kinds(events):
    return [e.kind for e in reversed(events.recent(CUPID.instrument_id, 100))]


def test_cupid_bid_wall_withdrawn_with_little_execution(setup):
    hub, engine, events = setup
    volume = 1_000_000
    for t, qty in ((0, 585_858), (1, 585_858), (2, 585_858), (3, 580_200), (4, 540_000)):
        volume += 500  # trades at 263.20, above the wall: none of them hit it
        hub.publish(book(t, bids_with_wall(qty), ASKS, ltp=263.20, volume=volume))
    detected = [e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_DETECTED"]
    assert len(detected) == 1 and detected[0].data["price"] == 263.00
    assert detected[0].data["orders"] == 184 and detected[0].data["size_vs_typical"] > 100

    # 11:02:06 - price breaks 263 while 21,400 trade on the way down; the rest of the wall is gone
    broken_bids = [
        (262.85, 2000, 7),
        (262.80, 3000, 9),
        (262.75, 2500, 8),
        (262.70, 2800, 9),
        (262.65, 3000, 9),
    ]
    broken_asks = [
        (262.90, 1500, 6),
        (262.95, 2000, 7),
        (263.00, 2200, 8),
        (263.05, 2400, 8),
        (263.10, 2600, 9),
    ]
    hub.publish(book(5, broken_bids, broken_asks, ltp=262.85, volume=volume + 21_400))

    withdrawn = next(e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_WITHDRAWN")
    data = withdrawn.data
    assert data["initial_quantity"] == 585_858 and data["initial_orders"] == 184
    assert data["executed_near_level"] == 21_400
    assert data["displayed_removed"] == pytest.approx(585_858)
    assert data["execution_share"] == pytest.approx(21_400 / 585_858, abs=1e-3)
    assert data["classification"] == "Large liquidity withdrawal"
    assert "does not establish manipulation" in data["disclaimer"]
    assert data["price_before"] == 263.20 and data["price_after"] == 262.85
    broken = next(e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_BROKEN")
    assert withdrawn.event_id in broken.parents
    assert engine.walls(CUPID.instrument_id)["history"][0]["state"] == "WITHDRAWN"


def test_ask_wall_consumed_by_buying(setup):
    hub, engine, events = setup
    bids = [(99.95, 100, 2), (99.90, 120, 3), (99.85, 90, 2), (99.80, 110, 3), (99.75, 100, 2)]
    asks = lambda q: [(100.00, q, 40), (100.05, 100, 2), (100.10, 90, 2), (100.15, 120, 3), (100.20, 100, 2)]  # noqa: E731
    hub.publish(book(0, bids, asks(10_000), ltp=99.95, volume=50_000))
    hub.publish(book(1, bids, asks(6_000), ltp=100.00, volume=54_000))  # 4,000 bought at the wall
    hub.publish(book(2, bids, asks(1_000), ltp=100.00, volume=59_000))
    after = [(100.05, 100, 2), (100.10, 90, 2), (100.15, 120, 3), (100.20, 100, 2), (100.25, 100, 2)]
    hub.publish(book(3, bids, after, ltp=100.05, volume=60_100))
    consumed = next(e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_CONSUMED")
    assert consumed.data["execution_share"] >= 0.8
    assert "WALL_WITHDRAWN" not in kinds(events)


def test_wall_pushed_out_of_view_is_not_a_cancellation(setup):
    hub, engine, events = setup
    hub.publish(book(0, bids_with_wall(500_000), ASKS, ltp=263.20, volume=1000))
    better = [(263.45, 3000, 9), (263.40, 2500, 8), (263.35, 4000, 9), (263.30, 3500, 9), (263.25, 3100, 9)]
    asks = [(263.50, 3000, 9), (263.55, 2500, 8), (263.60, 4000, 9), (263.65, 3500, 9), (263.70, 3100, 9)]
    hub.publish(book(1, better, asks, ltp=263.45, volume=1000))
    assert not {"WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_PARTIAL"} & set(kinds(events))
    assert engine.walls(CUPID.instrument_id)["active"][0]["state"] == "OUT_OF_VIEW"
    # back in view with the same quantity: still the same wall, never reported as removed
    hub.publish(book(2, bids_with_wall(500_000), ASKS, ltp=263.20, volume=1000))
    assert engine.walls(CUPID.instrument_id)["active"][0]["state"] == "ACTIVE"
    assert kinds(events).count("WALL_DETECTED") == 1


def test_withdrawn_wall_migrates_lower(setup):
    hub, engine, events = setup
    hub.publish(book(0, bids_with_wall(585_858), ASKS, ltp=263.20, volume=1000))
    moved = [
        (263.20, 3000, 10),
        (263.15, 2500, 9),
        (263.10, 4000, 11),
        (263.05, 3500, 12),
        (262.95, 300_000, 90),
    ]
    hub.publish(book(3, moved, ASKS, ltp=263.20, volume=1000))
    assert "WALL_WITHDRAWN" in kinds(events)
    migrated = next(e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_MIGRATED")
    assert migrated.data["from_price"] == 263.00 and migrated.data["to_price"] == 262.95
    withdrawn = next(e for e in events.recent(CUPID.instrument_id) if e.kind == "WALL_WITHDRAWN")
    assert withdrawn.event_id in migrated.parents


def test_same_wall_on_two_feeds_is_confirmed_not_added(setup):
    hub, engine, events = setup
    hub.publish(book(0, bids_with_wall(585_858), ASKS, source="FYERS", ltp=263.20, volume=1000))
    hub.publish(book(0.2, bids_with_wall(585_858), ASKS, source="DHAN", ltp=263.20, volume=1000))
    walls = engine.walls(CUPID.instrument_id)["active"]
    assert {w["source"] for w in walls} == {"FYERS", "DHAN"}
    dhan = next(w for w in walls if w["source"] == "DHAN")
    assert dhan["confirmed_by"] == ["FYERS"] and dhan["current_quantity"] == 585_858


def test_infer_tick_uses_volume_change_and_quote_rule():
    prev = book(0, bids_with_wall(1000), ASKS, ltp=263.20, volume=100)
    lifted = book(1, bids_with_wall(1000), ASKS, ltp=263.25, volume=160)
    tick = infer_tick(prev, lifted)
    assert tick.quantity == 60 and tick.side == BUY
    hit = book(2, bids_with_wall(1000), ASKS, ltp=263.20, volume=190)
    assert infer_tick(lifted, hit).side == SELL
    assert infer_tick(hit, book(3, bids_with_wall(1000), ASKS, ltp=263.20, volume=190)) is None


def test_hub_rejects_crossed_books_and_forwards_primary_only():
    clock = SimulatedClock(T0)
    forwarded = []
    hub = MarketDataHub(clock, forward_quote=forwarded.append, primary_for=lambda iid: "FYERS")
    hub.publish(book(0, [(100.0, 10, 1)], [(99.0, 10, 1)], source="FYERS"))  # crossed
    assert hub.sources()[0]["rejected"] == 1 and not forwarded
    hub.publish(book(1, [(100.0, 10, 1)], [(100.5, 10, 1)], source="FYERS"))
    hub.publish(book(1.5, [(100.1, 10, 1)], [(100.6, 10, 1)], source="DHAN"))
    assert [q.bid_price for q in forwarded] == [Decimal("100.0")]  # DHAN is not primary and FYERS is fresh
    hub.publish(book(8, [(100.2, 10, 1)], [(100.7, 10, 1)], source="DHAN"))  # FYERS quiet for 7 s
    assert forwarded[-1].bid_price == Decimal("100.2")
    assert set(hub.books(CUPID.instrument_id)) == {"FYERS", "DHAN"}


def test_expected_fill_walks_the_book():
    b = book(0, bids_with_wall(1000), ASKS)
    fill = expected_fill(b, BUY, 5000)
    assert fill["touch"] == 263.25 and fill["levels_used"] == 3
    assert fill["average_price"] == pytest.approx((3980 * 263.25 + 958 * 263.30 + 62 * 263.35) / 5000)
    assert fill["slippage"] > 0
