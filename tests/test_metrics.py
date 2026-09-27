import math
from datetime import UTC, datetime, timedelta

import pytest

from jdquant.analytics import metrics as m
from jdquant.strategy import indicators as ind


def test_twr_ignores_capital_flows():
    """AC-32002."""
    nav = [100, 110, 1110, 1221]
    flows = [0, 0, 1000, 0]
    assert m.time_weighted_return(nav, flows) == pytest.approx(1.1 * 1.0 * 1.1 - 1)


def test_ratio_metrics_need_minimum_periods():
    """AC-32003, FR-32052."""
    assert m.sharpe_ratio([0.01] * 10, 252) is None
    assert m.sortino_ratio([0.01] * 10, 252) is None


def test_sharpe_matches_reference_formula():
    returns = [0.01, -0.005, 0.002, 0.007, -0.003] * 8
    mean = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    assert m.sharpe_ratio(returns, 252) == pytest.approx(mean / sd * math.sqrt(252), rel=1e-12)


def test_drawdown_and_duration():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ts = [t0 + timedelta(days=i) for i in range(6)]
    nav = [100, 120, 90, 100, 125, 110]
    assert m.max_drawdown(nav) == pytest.approx(0.25)
    assert m.max_drawdown_duration(ts, nav) == pytest.approx(3.0)


def test_trade_statistics():
    stats = m.trade_statistics([10, -5, 20, -5])
    assert stats["win_rate"] == 0.5
    assert stats["profit_factor"] == 3.0
    assert stats["payoff_ratio"] == 3.0
    assert stats["expectancy"] == 5.0


def test_indicators():
    values = list(range(1, 21))
    assert ind.sma(values, 5) == 18
    assert ind.ema([1, 2, 3], 3) == 2
    assert ind.rsi(values, 14) == 100.0
    assert ind.rsi([10, 9, 8, 9, 10, 11, 10, 9, 10, 11, 12, 13, 12, 11, 12], 14) == pytest.approx(
        57.1428, rel=1e-3
    )
    lower, mid, upper = ind.bollinger([1, 2, 3, 4, 5], 5, 2)
    assert mid == 3 and upper - mid == pytest.approx(2 * math.sqrt(2))
    assert ind.atr([2, 3, 4], [1, 2, 3], [1.5, 2.5, 3.5], 2) == pytest.approx(1.5)
    assert ind.sma(values, 50) is None
