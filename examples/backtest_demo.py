"""Run every strategy template on deterministic synthetic BTC data and print a comparison."""

from datetime import UTC, datetime
from decimal import Decimal

from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.platform import demo_instruments
from jdquant.strategy.templates import TEMPLATES


def main() -> None:
    btc = demo_instruments()[0]
    candles = random_walk_candles(
        btc, datetime(2025, 1, 1, tzinfo=UTC), 2_000, start_price=Decimal(40_000), volatility=0.012, seed=42
    )
    print(f"{'strategy':<22}{'return':>9}{'sharpe':>9}{'max dd':>9}{'trades':>8}{'win %':>8}")
    for name in TEMPLATES:
        params = {"quantity": "0.5"}
        result = run_backtest(BacktestConfig(name, [btc], {btc.instrument_id: candles}, params))
        m = result.metrics
        sharpe = f"{m['sharpe_ratio']:.2f}" if m["sharpe_ratio"] is not None else "n/a"
        win = f"{m['win_rate'] * 100:.0f}" if m["win_rate"] is not None else "n/a"
        print(
            f"{name:<22}{m['total_return'] * 100:>8.2f}%{sharpe:>9}{m['max_drawdown'] * 100:>8.2f}%"
            f"{m['trade_count']:>8}{win:>8}"
        )


if __name__ == "__main__":
    main()
