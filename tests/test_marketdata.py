from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

from jdquant.core.clock import SimulatedClock
from jdquant.core.events import EventBus
from jdquant.marketdata.cache import FeedStatus, MarketDataCache
from jdquant.marketdata.candles import CandleAggregator, bucket_start
from jdquant.marketdata.records import Quote, Trade

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def _trades(n: int = 500) -> list[Trade]:
    rng = Random(1)
    ts, price, out = T0, Decimal(100), []
    for _ in range(n):
        ts += timedelta(seconds=rng.randint(1, 20))
        price += Decimal(rng.randint(-5, 5)) / 100
        out.append(Trade("X:Y", ts, price, Decimal(rng.randint(1, 9))))
    return out


def test_candles_match_offline_recomputation():
    """AC-20002."""
    trades = _trades()
    agg = CandleAggregator("X:Y", 60, emit_empty=False)
    candles = [c for t in trades for c in agg.on_trade(t)]
    candles += agg.flush(trades[-1].exchange_ts + timedelta(minutes=1))

    buckets: dict[datetime, list[Trade]] = {}
    for t in trades:
        buckets.setdefault(bucket_start(t.exchange_ts, 60), []).append(t)
    assert [c.open_ts for c in candles] == sorted(buckets)
    for candle in candles:
        group = buckets[candle.open_ts]
        assert candle.open == group[0].price and candle.close == group[-1].price
        assert candle.high == max(t.price for t in group) and candle.low == min(t.price for t in group)
        assert candle.volume == sum(t.quantity for t in group)
        assert candle.trade_count == len(group)
        assert candle.vwap == sum(t.price * t.quantity for t in group) / candle.volume


def test_empty_interval_uses_previous_close_with_zero_volume():
    """FR-20063."""
    agg = CandleAggregator("X:Y", 60)
    agg.on_trade(Trade("X:Y", T0 + timedelta(seconds=5), Decimal(10), Decimal(1)))
    closed = agg.on_trade(Trade("X:Y", T0 + timedelta(minutes=3, seconds=1), Decimal(12), Decimal(1)))
    assert [c.volume for c in closed] == [1, 0, 0]
    assert all(c.close == Decimal(10) for c in closed)


def test_reference_price_precedence_and_staleness():
    """BR-20-04, FR-20044, FR-20045."""
    clock = SimulatedClock(T0)
    bus = EventBus(clock)
    events = []
    bus.subscribe("marketdata.*", events.append)
    cache = MarketDataCache(clock, bus, stale_after=timedelta(seconds=10))
    cache.on_trade(Trade("X:Y", T0, Decimal(100), Decimal(1)))
    assert cache.reference_price("X:Y") == Decimal(100)
    cache.on_quote(Quote("X:Y", T0, Decimal(99), Decimal(1), Decimal(101.5), Decimal(1)))
    assert cache.reference_price("X:Y") == Decimal("100.25")
    cache.on_mark("X:Y", Decimal(100.1))
    assert cache.reference_price("X:Y") == Decimal(100.1)

    clock.set(T0 + timedelta(seconds=11))
    assert cache.status("X:Y") is FeedStatus.STALE
    cache.on_trade(Trade("X:Y", clock.now(), Decimal(100), Decimal(1)))
    assert cache.status("X:Y") is FeedStatus.LIVE
    assert [e.event_type for e in events] == ["marketdata.stale", "marketdata.recovered"]


def test_crossed_quotes_are_not_used_for_mid():
    cache = MarketDataCache(SimulatedClock(T0))
    cache.on_trade(Trade("X:Y", T0, Decimal(100), Decimal(1)))
    cache.on_quote(Quote("X:Y", T0, Decimal(101), Decimal(1), Decimal(100), Decimal(1)))
    assert cache.reference_price("X:Y") == Decimal(100)
