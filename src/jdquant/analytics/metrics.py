"""Shared performance metric library (Chapter 32, FR-32050)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

Number = float | int | Decimal
MIN_PERIODS = 30  # FR-32052


def simple_returns(nav: Sequence[Number], flows: Sequence[Number] | None = None) -> list[float]:
    """r_t = (NAV_t - NAV_{t-1} - F_t) / NAV_{t-1}, flows assumed at period end (FR-32001)."""
    values = [float(v) for v in nav]
    flow = [float(f) for f in flows] if flows is not None else [0.0] * len(values)
    return [
        (values[i] - values[i - 1] - flow[i]) / values[i - 1] for i in range(1, len(values)) if values[i - 1]
    ]


def time_weighted_return(nav: Sequence[Number], flows: Sequence[Number] | None = None) -> float:
    """Geometric linking of flow-adjusted sub-period returns (FR-32002)."""
    result = 1.0
    for r in simple_returns(nav, flows):
        result *= 1 + r
    return result - 1


def cagr(total_return: float, days: float) -> float | None:
    if days <= 0 or total_return <= -1:
        return None
    return (1 + total_return) ** (365.25 / days) - 1


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _sample_stdev(values: Sequence[float]) -> float:
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def annualized_volatility(returns: Sequence[float], periods_per_year: float) -> float | None:
    if len(returns) < MIN_PERIODS:
        return None
    return _sample_stdev(returns) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: Sequence[float], periods_per_year: float, risk_free: float = 0.0) -> float | None:
    if len(returns) < MIN_PERIODS:
        return None
    excess = [r - risk_free / periods_per_year for r in returns]
    sd = _sample_stdev(excess)
    return None if sd == 0 else _mean(excess) / sd * math.sqrt(periods_per_year)


def sortino_ratio(returns: Sequence[float], periods_per_year: float, mar: float = 0.0) -> float | None:
    if len(returns) < MIN_PERIODS:
        return None
    downside = math.sqrt(_mean([min(0.0, r - mar) ** 2 for r in returns]))
    return None if downside == 0 else (_mean(returns) - mar) / downside * math.sqrt(periods_per_year)


def max_drawdown(nav: Sequence[Number]) -> float:
    peak, worst = -math.inf, 0.0
    for v in (float(x) for x in nav):
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def max_drawdown_duration(timestamps: Sequence[datetime], nav: Sequence[Number]) -> float:
    """Longest time in days from a peak until NAV recovers above it (FR-32016)."""
    longest, peak_value, peak_time = 0.0, -math.inf, None
    for ts, v in zip(timestamps, (float(x) for x in nav), strict=True):
        if v >= peak_value:
            if peak_time is not None:
                longest = max(longest, (ts - peak_time).total_seconds() / 86400)
            peak_value, peak_time = v, ts
    if peak_time is not None and timestamps:
        longest = max(longest, (timestamps[-1] - peak_time).total_seconds() / 86400)
    return longest


def calmar_ratio(cagr_value: float | None, mdd: float) -> float | None:
    if cagr_value is None or mdd == 0:
        return None
    return cagr_value / mdd


def trade_statistics(pnls: Sequence[Number]) -> dict[str, float | int | None]:
    """Round-trip statistics (FR-32019 – FR-32022)."""
    values = [float(p) for p in pnls]
    wins = [p for p in values if p > 0]
    losses = [p for p in values if p < 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    return {
        "trade_count": len(values),
        "win_rate": len(wins) / len(values) if values else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": _mean(values) if values else None,
        "average_win": _mean(wins) if wins else None,
        "average_loss": _mean(losses) if losses else None,
        "payoff_ratio": (_mean(wins) / abs(_mean(losses))) if wins and losses else None,
        "largest_win": max(wins) if wins else None,
        "largest_loss": min(losses) if losses else None,
    }


def summarize(
    timestamps: Sequence[datetime],
    nav: Sequence[Number],
    periods_per_year: float,
    trade_pnls: Sequence[Number] = (),
) -> dict[str, float | int | None]:
    returns = simple_returns(nav)
    total = time_weighted_return(nav)
    days = (timestamps[-1] - timestamps[0]).total_seconds() / 86400 if len(timestamps) > 1 else 0.0
    growth = cagr(total, days)
    mdd = max_drawdown(nav)
    return {
        "total_return": total,
        "cagr": growth,
        "annualized_volatility": annualized_volatility(returns, periods_per_year),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year),
        "sortino_ratio": sortino_ratio(returns, periods_per_year),
        "max_drawdown": mdd,
        "max_drawdown_duration_days": max_drawdown_duration(timestamps, nav),
        "calmar_ratio": calmar_ratio(growth, mdd),
        **trade_statistics(trade_pnls),
    }
