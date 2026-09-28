"""Price and volume structure: VWAP, volume profile, market structure, key levels and the market regime.

All functions are pure: they take bars (`PriceBar`) and return numbers with the evidence behind them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from jdquant.intelligence.bars import TIMEFRAMES, PriceBar, atr, ema, resample, split_sessions
from jdquant.intelligence.common import mean, median

# ---- VWAP -----------------------------------------------------------------------------------------------


def vwap_series(bars: list[PriceBar], *, anchor: datetime | None = None) -> list[dict[str, Any]]:
    """Cumulative VWAP with 1 and 2 standard-deviation bands, from the first bar (or the anchor)."""
    out = []
    volume = turnover = turnover_sq = 0.0
    for bar in bars:
        if anchor is not None and bar.start < anchor:
            continue
        v = bar.volume or 0.0
        volume += v
        turnover += v * bar.typical
        turnover_sq += v * bar.typical * bar.typical
        if volume <= 0:
            continue
        vwap = turnover / volume
        sd = math.sqrt(max(0.0, turnover_sq / volume - vwap * vwap))
        out.append(
            {
                "time": int(bar.start.timestamp()),
                "vwap": vwap,
                "upper1": vwap + sd,
                "lower1": vwap - sd,
                "upper2": vwap + 2 * sd,
                "lower2": vwap - 2 * sd,
                "sd": sd,
                "close": bar.close,
            }
        )
    return out


def vwap_state(bars: list[PriceBar], tick: float, *, anchor: datetime | None = None) -> dict[str, Any] | None:
    """Where price stands against the session (or anchored) VWAP, its slope, and recent crosses."""
    series = vwap_series(bars, anchor=anchor)
    if not series:
        return None
    last = series[-1]
    price, vwap, sd = last["close"], last["vwap"], last["sd"]
    back = series[-11] if len(series) > 10 else series[0]
    slope_ticks = (vwap - back["vwap"]) / tick / max(1, min(10, len(series) - 1)) if tick else None
    cross = None
    for prev, cur in zip(series[-6:-1], series[-5:], strict=False):
        if prev["close"] < prev["vwap"] and cur["close"] > cur["vwap"]:
            cross = {"kind": "VWAP_RECLAIM", "time": cur["time"]}
        elif prev["close"] > prev["vwap"] and cur["close"] < cur["vwap"]:
            cross = {"kind": "VWAP_REJECTION", "time": cur["time"]}
    recent_volume = mean([b.volume for b in bars[-5:]])
    typical_volume = mean([b.volume for b in bars[-60:-5]]) if len(bars) > 10 else None
    return {
        "vwap": vwap,
        "price": price,
        "distance": price - vwap,
        "distance_pct": (price - vwap) / vwap * 100 if vwap else None,
        "distance_sd": (price - vwap) / sd if sd else None,
        "bands": {
            "upper1": last["upper1"],
            "lower1": last["lower1"],
            "upper2": last["upper2"],
            "lower2": last["lower2"],
        },
        "slope_ticks_per_bar": slope_ticks,
        "slope": "UP"
        if slope_ticks and slope_ticks > 0.2
        else "DOWN"
        if slope_ticks and slope_ticks < -0.2
        else "FLAT",
        "position": "ABOVE" if price > vwap else "BELOW" if price < vwap else "AT",
        "recent_cross": cross,
        "volume_vs_average": recent_volume / typical_volume if typical_volume else None,
        "anchored_at": anchor.isoformat() if anchor else None,
        "series": series[-240:],
    }


# ---- volume profile -------------------------------------------------------------------------------------


def volume_profile(
    bars: list[PriceBar] | None = None,
    *,
    at_price: dict[float, float] | None = None,
    tick: float,
    max_bins: int = 120,
    value_area: float = 0.70,
) -> dict[str, Any] | None:
    """POC, value area (VAH/VAL), high- and low-volume nodes.

    From traded volume at each price when available (`at_price`, built from ticks), otherwise from bars,
    spreading each bar's volume evenly over its range.
    """
    raw: dict[float, float] = {}
    if at_price:
        raw = {p: v for p, v in at_price.items() if v > 0}
    elif bars:
        for bar in bars:
            if bar.volume <= 0:
                continue
            steps = max(1, round(bar.range / tick))
            for k in range(steps + 1):
                price = bar.low + k * tick if steps > 1 else bar.typical
                raw[round(price, 8)] = raw.get(round(price, 8), 0.0) + bar.volume / (steps + 1)
    if not raw:
        return None
    low, high = min(raw), max(raw)
    width = max(tick, (high - low) / max_bins) if high > low else tick
    width = math.ceil(width / tick) * tick
    hist: dict[float, float] = {}
    for price, vol in raw.items():
        bucket = round(low + math.floor((price - low) / width + 1e-9) * width, 8)
        hist[bucket] = hist.get(bucket, 0.0) + vol
    prices = sorted(hist)
    vols = [hist[p] for p in prices]
    total = sum(vols)
    poc_i = max(range(len(vols)), key=lambda i: vols[i])
    lo_i = hi_i = poc_i
    covered = vols[poc_i]
    while covered < value_area * total and (lo_i > 0 or hi_i < len(vols) - 1):
        below = vols[lo_i - 1] if lo_i > 0 else -1
        above = vols[hi_i + 1] if hi_i < len(vols) - 1 else -1
        if above >= below:
            hi_i += 1
            covered += vols[hi_i]
        else:
            lo_i -= 1
            covered += vols[lo_i]
    smooth = [mean(vols[max(0, i - 1) : i + 2]) for i in range(len(vols))]
    avg = mean(smooth)
    hvn, lvn = [], []
    for i in range(1, len(smooth) - 1):
        if (
            smooth[i] >= smooth[i - 1]
            and smooth[i] >= smooth[i + 1]
            and smooth[i] >= 1.3 * avg
            and i != poc_i
        ):
            hvn.append(prices[i] + width / 2)
        if smooth[i] <= smooth[i - 1] and smooth[i] <= smooth[i + 1] and smooth[i] <= 0.5 * avg:
            lvn.append(prices[i] + width / 2)
    return {
        "poc": prices[poc_i] + width / 2,
        "vah": prices[hi_i] + width,
        "val": prices[lo_i],
        "hvn": hvn[:10],
        "lvn": lvn[:10],
        "bin_width": width,
        "total_volume": total,
        "histogram": [{"price": p + width / 2, "volume": v} for p, v in zip(prices, vols, strict=True)],
        "source": "trades" if at_price else "bars",
    }


def developing_poc(bars: list[PriceBar], tick: float, every: int = 5) -> list[dict[str, Any]]:
    """The POC as the session developed, sampled every few bars."""
    out = []
    for i in range(every, len(bars) + 1, every):
        profile = volume_profile(bars[:i], tick=tick)
        if profile:
            out.append({"time": int(bars[i - 1].start.timestamp()), "poc": profile["poc"]})
    return out


# ---- market structure -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Swing:
    kind: str  # HIGH or LOW
    price: float
    time: datetime
    index: int


def swings(bars: list[PriceBar], strength: int = 2) -> list[Swing]:
    """Fractal swing points: a high (low) with `strength` lower highs (higher lows) on each side."""
    out = []
    for i in range(strength, len(bars) - strength):
        window = bars[i - strength : i + strength + 1]
        if bars[i].high == max(b.high for b in window) and bars[i].high > bars[i - 1].high:
            out.append(Swing("HIGH", bars[i].high, bars[i].start, i))
        if bars[i].low == min(b.low for b in window) and bars[i].low < bars[i - 1].low:
            out.append(Swing("LOW", bars[i].low, bars[i].start, i))
    return out


def market_structure(bars: list[PriceBar], strength: int = 2) -> dict[str, Any]:
    """Trend from swing sequence, with breaks of structure (BOS) and changes of character (CHOCH).

    A close beyond the last swing high/low in the trend's direction is a BOS (continuation); against it
    (taking out the swing that defended the trend) is a CHOCH (possible reversal).
    """
    points = swings(bars, strength)
    highs = [s for s in points if s.kind == "HIGH"]
    lows = [s for s in points if s.kind == "LOW"]
    trend = "NEUTRAL"
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1].price > highs[-2].price, lows[-1].price > lows[-2].price
        lh, ll = highs[-1].price < highs[-2].price, lows[-1].price < lows[-2].price
        trend = "BULLISH" if hh and hl else "BEARISH" if lh and ll else "NEUTRAL"
    events = []
    running = "NEUTRAL"
    last_high = last_low = None
    pending = sorted(points, key=lambda s: s.index)
    j = 0
    for i, bar in enumerate(bars):
        while j < len(pending) and pending[j].index + strength <= i:  # a swing is known `strength` bars later
            if pending[j].kind == "HIGH":
                last_high = pending[j]
            else:
                last_low = pending[j]
            j += 1
        if last_high is not None and bar.close > last_high.price:
            kind = "BOS" if running in ("BULLISH", "NEUTRAL") else "CHOCH"
            events.append(
                {
                    "kind": kind,
                    "direction": "UP",
                    "time": int(bar.start.timestamp()),
                    "level": last_high.price,
                }
            )
            running, last_high = "BULLISH", None
        elif last_low is not None and bar.close < last_low.price:
            kind = "BOS" if running in ("BEARISH", "NEUTRAL") else "CHOCH"
            events.append(
                {
                    "kind": kind,
                    "direction": "DOWN",
                    "time": int(bar.start.timestamp()),
                    "level": last_low.price,
                }
            )
            running, last_low = "BEARISH", None
    if trend == "NEUTRAL" and running != "NEUTRAL" and events:
        trend = running
    if trend == "NEUTRAL" and len(bars) >= 10 and (len(highs) < 2 or len(lows) < 2):
        # a steady move leaves no swings to compare: judge it by how far and how directly price travelled
        closes = [b.close for b in bars[-30:]]
        path = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
        net = closes[-1] - closes[0]
        typical = median([b.range for b in bars[-30:]])
        if path and abs(net) / path >= 0.5 and abs(net) >= 3 * typical:
            trend = "BULLISH" if net > 0 else "BEARISH"
    return {
        "trend": trend,
        "swing_highs": [{"price": s.price, "time": int(s.time.timestamp())} for s in highs[-5:]],
        "swing_lows": [{"price": s.price, "time": int(s.time.timestamp())} for s in lows[-5:]],
        "events": events[-10:],
        "last_event": events[-1] if events else None,
    }


def multi_timeframe(
    minute_bars: list[PriceBar],
    *,
    daily_bars: list[PriceBar] | None = None,
    day_of: Callable[[datetime], date] | None = None,
    frames: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1H", "4H", "D"),
) -> list[dict[str, Any]]:
    """Structure on each timeframe, highest first, so a pullback can be told from a reversal."""
    out = []
    for name in reversed(frames):
        seconds = TIMEFRAMES[name]
        if name == "D" and daily_bars:
            bars = daily_bars
        else:
            bars = resample(minute_bars, seconds, day_of=day_of) if seconds > 60 else minute_bars
        if len(bars) < 7:
            out.append({"timeframe": name, "trend": None, "bars": len(bars)})
            continue
        s = market_structure(bars)
        out.append(
            {
                "timeframe": name,
                "trend": s["trend"],
                "bars": len(bars),
                "last_event": s["last_event"],
                "last_swing_high": s["swing_highs"][-1]["price"] if s["swing_highs"] else None,
                "last_swing_low": s["swing_lows"][-1]["price"] if s["swing_lows"] else None,
            }
        )
    return out


def alignment(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize multi-timeframe trends: aligned, pullback within a trend, or conflicting."""
    trends = [(f["timeframe"], f["trend"]) for f in frames if f.get("trend")]
    if not trends:
        return {"verdict": "Not enough data", "bullish": 0, "bearish": 0}
    higher = [t for _, t in trends[: max(1, len(trends) // 2)]]
    lower = [t for _, t in trends[max(1, len(trends) // 2) :]]
    bull, bear = sum(t == "BULLISH" for _, t in trends), sum(t == "BEARISH" for _, t in trends)
    higher_bias = (
        max(("BULLISH", "BEARISH"), key=higher.count)
        if higher.count("BULLISH") != higher.count("BEARISH")
        else "NEUTRAL"
    )
    opposite = "BEARISH" if higher_bias == "BULLISH" else "BULLISH"
    if higher_bias != "NEUTRAL" and lower and all(t == higher_bias for t in lower):
        verdict = f"Aligned {higher_bias.lower()} across timeframes"
    elif higher_bias != "NEUTRAL" and opposite in lower and higher.count(higher_bias) == len(higher):
        verdict = f"Pullback within a {higher_bias.lower()} higher-timeframe trend"
    elif higher_bias != "NEUTRAL" and lower.count(opposite) > len(lower) // 2 and higher.count(opposite):
        verdict = "Possible reversal: lower timeframes and part of the higher ones have turned"
    else:
        verdict = "Mixed"
    return {"verdict": verdict, "bullish": bull, "bearish": bear, "higher_timeframe_bias": higher_bias}


# ---- key levels -----------------------------------------------------------------------------------------


def key_levels(
    bars: list[PriceBar],
    day_of: Callable[[datetime], date],
    *,
    opening_range_minutes: int = 15,
    atr_value: float | None = None,
) -> dict[str, Any]:
    """Previous day high/low/close, today's high/low, the opening range, and support/resistance zones."""
    days = split_sessions(bars, day_of)
    ordered = sorted(days)
    out: dict[str, Any] = {}
    if ordered:
        today = days[ordered[-1]]
        out["day_high"] = max(b.high for b in today)
        out["day_low"] = min(b.low for b in today)
        out["day_open"] = today[0].open
        start = today[0].start
        opening = [b for b in today if b.start < start + timedelta(minutes=opening_range_minutes)]
        if opening:
            out["opening_range"] = {
                "high": max(b.high for b in opening),
                "low": min(b.low for b in opening),
                "minutes": opening_range_minutes,
                "complete": today[-1].start >= start + timedelta(minutes=opening_range_minutes),
            }
    if len(ordered) >= 2:
        prev = days[ordered[-2]]
        out["previous_day"] = {
            "high": max(b.high for b in prev),
            "low": min(b.low for b in prev),
            "close": prev[-1].close,
            "date": ordered[-2].isoformat(),
        }
    points = swings(bars[-400:], 3)
    tolerance = (atr_value or median([b.range for b in bars[-50:]]) or 0) * 0.5
    zones: list[dict[str, Any]] = []
    for s in sorted(points, key=lambda s: s.price):
        if zones and tolerance and s.price - zones[-1]["high"] <= tolerance:
            z = zones[-1]
            z["high"] = max(z["high"], s.price)
            z["touches"] += 1
            z["prices"].append(s.price)
        else:
            zones.append({"low": s.price, "high": s.price, "touches": 1, "prices": [s.price]})
    last = bars[-1].close if bars else None
    strong = [z for z in zones if z["touches"] >= 2]
    for z in strong:
        z["price"] = mean(z.pop("prices"))
        z["kind"] = "RESISTANCE" if last is not None and z["price"] > last else "SUPPORT"
    for z in zones:
        z.pop("prices", None)
    out["zones"] = sorted(strong, key=lambda z: abs(z["price"] - (last or 0)))[:8]
    return out


# ---- regime ---------------------------------------------------------------------------------------------


def regime(
    bars: list[PriceBar],
    *,
    session_minutes_elapsed: float | None = None,
    session_minutes_left: float | None = None,
    breadth: float | None = None,
    lookback: int = 20,
) -> dict[str, Any]:
    """Classify the market: trend, volatility, range vs breakout vs mean reversion, session effects.

    Evidence is returned with every label, and confidence reflects how much of it agrees.
    """
    if len(bars) < lookback + 5:
        return {"state": "UNKNOWN", "labels": [], "confidence": 0, "evidence": ["Not enough bars"]}
    closes = [b.close for b in bars]
    window = closes[-lookback - 1 :]
    path = sum(abs(b - a) for a, b in zip(window, window[1:], strict=False))
    efficiency = abs(window[-1] - window[0]) / path if path else 0.0
    ranges = atr(bars)
    atr_now = ranges[-1]
    history = ranges[-100:]
    rank = sum(1 for r in history if r <= atr_now) / len(history)
    fast, slow = ema(closes, 10), ema(closes, 30)
    slope = (slow[-1] - slow[-6]) / atr_now if atr_now else 0.0
    structure = market_structure(bars[-150:])
    prior_high = max(b.high for b in bars[-lookback - 1 : -1])
    prior_low = min(b.low for b in bars[-lookback - 1 : -1])
    returns = [b / a - 1 for a, b in zip(closes[-51:], closes[-50:], strict=False) if a]
    autocorr = _autocorrelation(returns)

    evidence, labels = [], []
    direction = (
        "UP" if fast[-1] > slow[-1] and slope > 0 else "DOWN" if fast[-1] < slow[-1] and slope < 0 else "FLAT"
    )
    trending = efficiency >= 0.35 and direction != "FLAT"
    ranging = efficiency < 0.2
    evidence.append(
        f"Efficiency ratio {efficiency:.2f} over {lookback} bars (above 0.35 trends, below 0.20 ranges)"
    )
    evidence.append(
        f"Short average {'above' if fast[-1] > slow[-1] else 'below'} long; slope {slope:+.2f} ATR per 5 bars"
    )
    evidence.append(f"Structure {structure['trend'].lower()}")
    if trending:
        labels.append("TRENDING_UP" if direction == "UP" else "TRENDING_DOWN")
    elif ranging:
        labels.append("RANGE_BOUND")
    if closes[-1] > prior_high:
        labels.append("BREAKOUT")
        evidence.append(f"Close above the {lookback}-bar high {prior_high:g}")
    elif closes[-1] < prior_low:
        labels.append("BREAKDOWN")
        evidence.append(f"Close below the {lookback}-bar low {prior_low:g}")
    if rank >= 0.8:
        labels.append("HIGH_VOLATILITY")
    elif rank <= 0.2:
        labels.append("LOW_VOLATILITY")
    evidence.append(f"ATR at the {rank:.0%} percentile of the last {len(history)} bars")
    if autocorr is not None and autocorr < -0.15 and not trending:
        labels.append("MEAN_REVERTING")
        evidence.append(f"Returns reverse bar to bar (autocorrelation {autocorr:+.2f})")
    if session_minutes_elapsed is not None and session_minutes_elapsed <= 30 and rank >= 0.6:
        labels.append("OPENING_VOLATILITY")
    if session_minutes_left is not None and session_minutes_left <= 30 and rank >= 0.6:
        labels.append("CLOSING_VOLATILITY")
    if breadth is not None:
        evidence.append(f"Breadth {breadth:.0%} of tracked instruments up on the day")

    votes = {
        "UP": [
            direction == "UP",
            structure["trend"] == "BULLISH",
            slope > 0,
            breadth is not None and breadth > 0.55,
        ],
        "DOWN": [
            direction == "DOWN",
            structure["trend"] == "BEARISH",
            slope < 0,
            breadth is not None and breadth < 0.45,
        ],
    }
    lean = max(votes, key=lambda k: sum(votes[k]))
    agree = sum(votes[lean])
    considered = 3 + (breadth is not None)
    if trending:
        confidence = 50 + 50 * agree / considered * min(1.0, efficiency / 0.6)
    elif ranging:
        confidence = 50 + 40 * (0.2 - efficiency) / 0.2
    else:
        confidence = 35 + 10 * agree / considered
    state = labels[0] if labels else "TRANSITIONAL"
    return {
        "state": state,
        "labels": labels,
        "trend": {"UP": "Bullish", "DOWN": "Bearish"}.get(direction, "Sideways")
        if trending
        else (
            "Sideways"
            if ranging
            else {"UP": "Leaning bullish", "DOWN": "Leaning bearish"}.get(direction, "Sideways")
        ),
        "volatility": "Elevated" if rank >= 0.8 else "Low" if rank <= 0.2 else "Normal",
        "structure": structure["trend"].title(),
        "efficiency": round(efficiency, 3),
        "atr": atr_now,
        "atr_percentile": round(rank, 3),
        "autocorrelation": None if autocorr is None else round(autocorr, 3),
        "breadth": breadth,
        "confidence": round(max(0.0, min(100.0, confidence))),
        "evidence": evidence,
    }


def _autocorrelation(values: list[float]) -> float | None:
    if len(values) < 10:
        return None
    m = mean(values)
    num = sum((a - m) * (b - m) for a, b in zip(values, values[1:], strict=False))
    den = sum((v - m) ** 2 for v in values)
    return num / den if den else None
