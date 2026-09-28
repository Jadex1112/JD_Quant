"""A float bar type for analytics, conversions from the platform's bar sources, and resampling."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class PriceBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    buy: float | None = None  # buyer-initiated volume, when built from trades
    sell: float | None = None

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def delta(self) -> float | None:
        return None if self.buy is None or self.sell is None else self.buy - self.sell


def from_candles(candles) -> list[PriceBar]:
    """From `marketdata.records.Candle` (Decimal) objects."""
    return [
        PriceBar(c.open_ts, float(c.open), float(c.high), float(c.low), float(c.close), float(c.volume))
        for c in candles
    ]


def from_flow(bars) -> list[PriceBar]:
    """From order-flow bars, keeping buy and sell volume."""
    return [PriceBar(b.start, b.open, b.high, b.low, b.close, b.volume, b.buy, b.sell) for b in bars]


def from_dicts(rows) -> list[PriceBar]:
    """From chart-style dicts with `time` (epoch seconds) or `open_ts`."""
    out = []
    for r in rows:
        start = r.get("open_ts") or datetime.fromtimestamp(r["time"]).astimezone()
        out.append(PriceBar(start, r["open"], r["high"], r["low"], r["close"], r.get("volume") or 0.0))
    return out


def merge(*sources: Iterable[PriceBar]) -> list[PriceBar]:
    """Combine bar lists; for the same start time the later source wins (e.g. live over history)."""
    by_start: dict[datetime, PriceBar] = {}
    for bars in sources:
        for bar in bars:
            by_start[bar.start] = bar
    return [by_start[k] for k in sorted(by_start)]


def resample(bars: list[PriceBar], seconds: int, *, day_of: Callable[[datetime], date] | None = None):
    """Aggregate to a longer interval. Daily bars follow the session day when `day_of` is given."""
    out: list[PriceBar] = []
    for bar in bars:
        if seconds >= 86400 and day_of is not None:
            day = day_of(bar.start)
            key = datetime(day.year, day.month, day.day, tzinfo=bar.start.tzinfo)
        else:
            epoch = int(bar.start.timestamp())
            key = datetime.fromtimestamp(epoch - epoch % seconds, bar.start.tzinfo)
        last = out[-1] if out else None
        if last is not None and last.start == key:
            buy = None if last.buy is None or bar.buy is None else last.buy + bar.buy
            sell = None if last.sell is None or bar.sell is None else last.sell + bar.sell
            out[-1] = PriceBar(
                key,
                last.open,
                max(last.high, bar.high),
                min(last.low, bar.low),
                bar.close,
                last.volume + bar.volume,
                buy,
                sell,
            )
        else:
            out.append(PriceBar(key, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.buy, bar.sell))
    return out


def split_sessions(bars: list[PriceBar], day_of: Callable[[datetime], date]) -> dict[date, list[PriceBar]]:
    days: dict[date, list[PriceBar]] = {}
    for bar in bars:
        days.setdefault(day_of(bar.start), []).append(bar)
    return days


def atr(bars: list[PriceBar], period: int = 14) -> list[float]:
    """Average true range (Wilder), one value per bar from the first."""
    out: list[float] = []
    prev_close = None
    value = None
    for bar in bars:
        tr = (
            bar.range
            if prev_close is None
            else max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        )
        value = tr if value is None else (value * (period - 1) + tr) / period
        out.append(value)
        prev_close = bar.close
    return out


def ema(values: list[float], period: int) -> list[float]:
    out: list[float] = []
    k = 2 / (period + 1)
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


TIMEFRAMES = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "D": 86400,
}


def minutes_since(start: datetime, at: datetime) -> float:
    return (at - start) / timedelta(minutes=1)
