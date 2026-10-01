"""VWAP, volume profile, market structure, key levels, multi-timeframe and regime."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from jdquant.intelligence.bars import PriceBar, resample
from jdquant.intelligence.price import (
    alignment,
    key_levels,
    market_structure,
    multi_timeframe,
    regime,
    volume_profile,
    vwap_state,
)

T0 = datetime(2026, 9, 28, 3, 45, tzinfo=UTC)  # 09:15 IST


def bar(i, o, h, low, c, v=100.0, start=T0):
    return PriceBar(start + timedelta(minutes=i), o, h, low, c, v)


def path_bars(prices, volume=100.0, start=T0):
    out = []
    for i, (a, b) in enumerate(zip(prices, prices[1:], strict=False)):
        out.append(bar(i, a, max(a, b) + 0.05, min(a, b) - 0.05, b, volume, start))
    return out


def test_vwap_is_volume_weighted_typical_price():
    bars = [bar(0, 100, 101, 99, 100, 100), bar(1, 100, 103, 100, 102, 300)]
    state = vwap_state(bars, 0.05)
    expected = (100 * 100 + 300 * (103 + 100 + 102) / 3) / 400
    assert state["vwap"] == pytest.approx(expected)
    assert state["position"] == "ABOVE" and state["bands"]["upper1"] > state["vwap"]


def test_vwap_reclaim_detected():
    prices = [100 - 0.1 * k for k in range(20)] + [98.0 + 0.4 * k for k in range(8)]
    state = vwap_state(path_bars(prices), 0.05)
    assert state["recent_cross"]["kind"] == "VWAP_RECLAIM"


def test_volume_profile_poc_and_value_area():
    at_price = {100.0: 50, 100.05: 80, 100.10: 400, 100.15: 90, 100.20: 60, 100.25: 5, 100.30: 5}
    profile = volume_profile(at_price=at_price, tick=0.05)
    assert profile["poc"] == pytest.approx(100.10 + profile["bin_width"] / 2)
    inside = sum(v for p, v in at_price.items() if profile["val"] - 1e-9 <= p < profile["vah"])
    assert inside >= 0.7 * sum(at_price.values())
    assert profile["source"] == "trades"


def test_market_structure_bullish_then_change_of_character():
    zig = [100, 102, 101, 104, 103, 106, 105, 108, 107, 110]
    prices = []
    for a, b in zip(zig, zig[1:], strict=False):
        prices += [a + (b - a) * k / 4 for k in range(4)]
    s = market_structure(path_bars(prices + [110]))
    assert s["trend"] == "BULLISH"
    down = path_bars(prices + [110, 108, 106, 104, 103])
    events = market_structure(down)["events"]
    assert events[-1]["direction"] == "DOWN" and events[-1]["kind"] == "CHOCH"


def test_key_levels_previous_day_and_opening_range():
    ist_day = lambda t: (t + timedelta(hours=5, minutes=30)).date()  # noqa: E731
    yesterday = [
        bar(i, 100, 101 + (i == 10), 99 - (i == 20), 100.5, start=T0 - timedelta(days=1)) for i in range(60)
    ]
    today = [bar(i, 100, 100.5 + i * 0.01, 99.5, 100.2) for i in range(30)]
    levels = key_levels(yesterday + today, ist_day)
    assert levels["previous_day"]["high"] == 102 and levels["previous_day"]["low"] == 98
    assert levels["opening_range"]["complete"] is True and levels["opening_range"]["minutes"] == 15


def test_regime_trend_range_and_breakout():
    trend = path_bars([100 + 0.3 * k + 0.2 * math.sin(k) for k in range(120)])
    r = regime(trend)
    assert "TRENDING_UP" in r["labels"] and r["trend"] == "Bullish" and r["confidence"] >= 60
    chop = path_bars([100 + (0.5 if k % 2 else -0.5) + 0.05 * math.sin(k / 7) for k in range(120)])
    r = regime(chop)
    assert "RANGE_BOUND" in r["labels"] and "MEAN_REVERTING" in r["labels"]
    breakout = path_bars([100 + 0.2 * math.sin(k) for k in range(80)] + [100.5, 101.5, 103])
    assert "BREAKOUT" in regime(breakout)["labels"]


def test_multi_timeframe_and_alignment():
    minutes = path_bars([100 + 0.05 * k + 0.3 * math.sin(k / 5) for k in range(2000)])
    frames = multi_timeframe(minutes, frames=("5m", "15m", "1H"))
    assert [f["timeframe"] for f in frames] == ["1H", "15m", "5m"]
    assert frames[0]["trend"] == "BULLISH"
    assert "bullish" in alignment(frames)["verdict"].lower() or alignment(frames)["bullish"] >= 2
    assert len(resample(minutes, 900)) == math.ceil(len(minutes) / 15)
