from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import ValidationError
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.records import Candle
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.platform import demo_instruments
from jdquant.strategy.base import Strategy

BTC = demo_instruments()[0]
START = datetime(2025, 1, 1, tzinfo=UTC)


def _data(n=600, seed=3):
    return {BTC.instrument_id: random_walk_candles(BTC, START, n, start_price=Decimal(30000), seed=seed)}


def test_backtest_is_reproducible():
    """AC-25002, FR-25009."""
    cfg = lambda: BacktestConfig("ma_crossover", [BTC], _data(), {"fast": 5, "slow": 20, "quantity": "0.1"})  # noqa: E731
    a, b = run_backtest(cfg()), run_backtest(cfg())
    assert a.reproducibility_hash == b.reproducibility_hash
    assert [(f.exchange_ts, f.price, f.quantity) for f in a.fills] == [
        (f.exchange_ts, f.price, f.quantity) for f in b.fills
    ]
    assert a.metrics == b.metrics and a.final_equity == b.final_equity
    assert len(a.trades) > 5


def test_equity_reconciles_with_trades():
    result = run_backtest(
        BacktestConfig(
            "ma_crossover",
            [BTC],
            _data(),
            {"fast": 5, "slow": 20, "quantity": "0.1"},
            fees=FeeSchedule(Decimal(10), Decimal(10)),
        )
    )
    open_value = result.final_equity - Decimal(100_000) - sum(t.net_pnl for t in result.trades)
    last_close = _data()[BTC.instrument_id][-1].close
    unmatched = sum(f.quantity * f.side.sign for f in result.fills)
    if unmatched == 0:
        assert open_value == 0
    else:
        assert abs(open_value) < abs(unmatched) * last_close


class _Peek(Strategy):
    name = "peek"
    seen: list = []

    def on_bar(self, candle):
        history = self.ctx.candles(candle.instrument_id)
        _Peek.seen.append((self.ctx.now(), history[-1].close_ts, len(history)))


def test_no_look_ahead():
    """AC-25001, FR-25003."""
    _Peek.seen = []
    run_backtest(BacktestConfig(_Peek, [BTC], _data(50)))
    for now, last_close_ts, _ in _Peek.seen:
        assert last_close_ts <= now
    assert [n for _, _, n in _Peek.seen] == list(range(1, 51))


class _BuyOnce(Strategy):
    name = "buy_once"

    def on_bar(self, candle):
        if not self.ctx.state.get("done"):
            self.ctx.state["done"] = True
            self.ctx.buy(candle.instrument_id, Decimal("1"))


def test_market_orders_fill_at_next_bar_open():
    """AC-25004, BR-25-03."""
    data = _data(5)
    result = run_backtest(BacktestConfig(_BuyOnce, [BTC], data, slippage_bps=Decimal(0)))
    candles = data[BTC.instrument_id]
    assert len(result.fills) == 1
    assert result.fills[0].price == candles[1].open
    assert result.fills[0].exchange_ts == candles[1].open_ts


def test_invalid_config_reports_all_errors():
    """FR-25022."""
    other = demo_instruments()[1]
    with pytest.raises(ValidationError) as err:
        run_backtest(
            BacktestConfig("ma_crossover", [BTC, other], {BTC.instrument_id: []}, initial_capital=Decimal(0))
        )
    assert len(err.value.violations) == 3


def test_unknown_parameter_rejected():
    with pytest.raises(ValidationError) as err:
        run_backtest(BacktestConfig("ma_crossover", [BTC], _data(60), {"fastt": 3}))
    assert err.value.code == "PARAMETER_INVALID"


class _Broken(Strategy):
    name = "broken"

    def on_bar(self, candle):
        raise RuntimeError("bug")


def test_repeated_strategy_errors_fail_the_deployment():
    """FR-24022: three errors within 60 s of simulated time fail the deployment."""
    candles = random_walk_candles(BTC, START, 20, interval_seconds=10, start_price=Decimal(30000))
    result = run_backtest(BacktestConfig(_Broken, [BTC], {BTC.instrument_id: candles}))
    assert result.strategy_errors == 3


def test_all_templates_run():
    from jdquant.strategy.templates import TEMPLATES

    for name in TEMPLATES:
        result = run_backtest(BacktestConfig(name, [BTC], _data(400)))
        assert result.equity_curve


def test_limit_orders_need_price_through():
    class _Bid(Strategy):
        name = "bid"

        def on_bar(self, candle):
            if not self.ctx.state.get("done"):
                self.ctx.state["done"] = True
                self.ctx.buy(candle.instrument_id, Decimal(1), limit_price=candle.low)

    step = timedelta(hours=1)
    candles = [
        Candle(
            BTC.instrument_id,
            3600,
            START + step * i,
            START + step * (i + 1),
            Decimal(o),
            Decimal(h),
            Decimal(lo),
            Decimal(c),
            Decimal(10),
        )
        for i, (o, h, lo, c) in enumerate([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, "98.99", 99)])
    ]
    result = run_backtest(BacktestConfig(_Bid, [BTC], {BTC.instrument_id: candles}))
    assert [f.exchange_ts for f in result.fills] == [candles[2].open_ts]
    assert result.fills[0].price == Decimal(99)
