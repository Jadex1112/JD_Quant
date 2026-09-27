"""Indicator library (FR-24009). Statistical outputs are floats (permitted by CON-022)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

Number = float | int | Decimal


def _floats(values: Sequence[Number]) -> list[float]:
    return [float(v) for v in values]


def sma(values: Sequence[Number], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(_floats(values[-period:])) / period


def ema(values: Sequence[Number], period: int) -> float | None:
    """EMA seeded with the SMA of the first `period` values."""
    if period <= 0 or len(values) < period:
        return None
    data = _floats(values)
    alpha = 2 / (period + 1)
    result = sum(data[:period]) / period
    for v in data[period:]:
        result = alpha * v + (1 - alpha) * result
    return result


def stdev(values: Sequence[Number], period: int) -> float | None:
    """Population standard deviation over the last `period` values."""
    if period <= 1 or len(values) < period:
        return None
    window = _floats(values[-period:])
    mean = sum(window) / period
    return math.sqrt(sum((v - mean) ** 2 for v in window) / period)


def zscore(values: Sequence[Number], period: int) -> float | None:
    mean, sd = sma(values, period), stdev(values, period)
    if mean is None or not sd:
        return None
    return (float(values[-1]) - mean) / sd


def bollinger(
    values: Sequence[Number], period: int = 20, k: float = 2.0
) -> tuple[float, float, float] | None:
    mid, sd = sma(values, period), stdev(values, period)
    if mid is None or sd is None:
        return None
    return mid - k * sd, mid, mid + k * sd


def rsi(values: Sequence[Number], period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(values) <= period:
        return None
    data = _floats(values)
    gains = [max(data[i] - data[i - 1], 0.0) for i in range(1, len(data))]
    losses = [max(data[i - 1] - data[i], 0.0) for i in range(1, len(data))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr(
    highs: Sequence[Number], lows: Sequence[Number], closes: Sequence[Number], period: int = 14
) -> float | None:
    """Wilder's Average True Range."""
    if not (len(highs) == len(lows) == len(closes)) or len(closes) <= period:
        return None
    h, low, c = _floats(highs), _floats(lows), _floats(closes)
    trs = [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, len(c))]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def donchian(highs: Sequence[Number], lows: Sequence[Number], period: int) -> tuple[float, float] | None:
    if len(highs) < period or len(lows) < period:
        return None
    return min(_floats(lows[-period:])), max(_floats(highs[-period:]))


def ema_series(values: Sequence[Number], period: int) -> list[float]:
    """EMA at every point from index period-1 on (seeded with the SMA of the first `period` values)."""
    data = _floats(values)
    if period <= 0 or len(data) < period:
        return []
    alpha = 2 / (period + 1)
    out = [sum(data[:period]) / period]
    for v in data[period:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def macd(
    values: Sequence[Number], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float] | None:
    """(MACD line, signal line) at the last value."""
    fast_ema, slow_ema = ema_series(values, fast), ema_series(values, slow)
    if not slow_ema:
        return None
    line = [f - s for f, s in zip(fast_ema[slow - fast :], slow_ema, strict=True)]
    signal_line = ema_series(line, signal)
    if not signal_line:
        return None
    return line[-1], signal_line[-1]


def rate_of_change(values: Sequence[Number], period: int) -> float | None:
    if period <= 0 or len(values) <= period or not float(values[-period - 1]):
        return None
    return float(values[-1]) / float(values[-period - 1]) - 1


def supertrend(
    highs: Sequence[Number],
    lows: Sequence[Number],
    closes: Sequence[Number],
    period: int = 10,
    k: float = 3.0,
) -> tuple[bool, float] | None:
    """(uptrend?, active band) at the last bar: ATR bands around the bar midpoint that only ratchet
    in the trend's direction; the trend flips when a close crosses the opposite band."""
    n = len(closes)
    if not (len(highs) == len(lows) == n) or n <= period + 1:
        return None
    h, low, c = _floats(highs), _floats(lows), _floats(closes)
    trs = [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, n)]
    atr_value = sum(trs[:period]) / period
    upper = lower = None
    up = True
    for i in range(period, n):
        if i > period:
            atr_value = (atr_value * (period - 1) + trs[i - 1]) / period
        mid = (h[i] + low[i]) / 2
        basic_upper, basic_lower = mid + k * atr_value, mid - k * atr_value
        upper = basic_upper if upper is None or basic_upper < upper or c[i - 1] > upper else upper
        lower = basic_lower if lower is None or basic_lower > lower or c[i - 1] < lower else lower
        if up and c[i] < lower:
            up = False
        elif not up and c[i] > upper:
            up = True
    return up, (lower if up else upper)
