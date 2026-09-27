"""Forex and gold research: candidates per market, shorting, the London breakout, FX rates, schedule."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import set_quote

from jdquant.autopilot import research as research_module
from jdquant.autopilot.research import Candidate, ResearchConfig, applies, candidate_grid, strategy_parameters
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.marketdata.records import Candle
from jdquant.markets.forex import FX_FEES
from jdquant.platform import fx_instrument

EUR = fx_instrument("EUR_USD")
GOLD = fx_instrument("XAU_USD")


def test_candidates_follow_the_market():
    from test_autopilot import nse

    grid = candidate_grid(900)
    fx = {c.signal for c in grid if applies(c, EUR)}
    stock = {c.signal for c in grid if applies(c, nse("TREND-EQ"))}
    assert "london_breakout" in fx and "orb" not in fx and "volume_breakout" not in fx
    assert "london_breakout" not in stock and "orb" in stock
    assert "london_breakout" not in {c.signal for c in candidate_grid(86400)}  # needs intraday bars


def test_forex_and_gold_trend_signals_may_short():
    config = ResearchConfig(fx_rates={"INR": Decimal(1), "USD": Decimal(85)})
    trend = strategy_parameters(Candidate("ewmac", (("fast", 8),)), Decimal(1000), config, GOLD)
    reversion = strategy_parameters(Candidate("rsi", (("period", 14),)), Decimal(1000), config, GOLD)
    assert trend["allow_short"] is True and reversion["allow_short"] is False
    assert EUR.can_short and not research_module.slippage_for(EUR, config) > Decimal("0.5")


def _day(prices_by_hour: dict[float, float], day=datetime(2026, 9, 29, tzinfo=UTC)) -> list[Candle]:
    """15-minute EUR/USD bars for one London day (BST = UTC+1), flat except where given."""
    candles, price = [], 1.17
    for k in range(4 * 24):
        opened = day - timedelta(hours=1) + timedelta(minutes=15 * k)  # from 00:00 London
        hour = k / 4
        price = prices_by_hour.get(hour, price)
        p = Decimal(f"{price:.5f}")
        candles.append(
            Candle(EUR.instrument_id, 900, opened, opened + timedelta(minutes=15), p,
                   p + Decimal("0.0002"), p - Decimal("0.0002"), p, Decimal(10))
        )  # fmt: skip
    return candles


def _run(candles, allow_short=True):
    return run_backtest(
        BacktestConfig(
            "autopilot",
            [EUR],
            {EUR.instrument_id: candles},
            parameters={
                "signal": "london_breakout",
                "period": 4,
                "capital": "10000",
                "stop_loss": "0",
                "allow_short": allow_short,
            },
            initial_capital=Decimal(10000),
            base_currency="USD",
            fees=FX_FEES,
            slippage_bps=Decimal(0),
        )
    )


def test_london_breakout_trades_the_asian_range_and_is_flat_by_the_cutoff():
    up = _run(_day({8.0: 1.1720, 12.0: 1.1760}))  # breaks the 00:00-07:00 range upwards at 08:00 London
    buy, sell = up.fills
    assert buy.side.value == "BUY" and buy.exchange_ts == datetime(2026, 9, 29, 7, 15, tzinfo=UTC)
    # Closed at the 16:45 New York intraday cutoff (20:45 UTC), before the rollover's financing.
    assert sell.side.value == "SELL" and sell.exchange_ts == datetime(2026, 9, 29, 20, 45, tzinfo=UTC)
    assert up.trades[0].net_pnl > 0
    down = _run(_day({9.0: 1.1680}))
    assert down.fills[0].side.value == "SELL"  # a downside break goes short
    assert _run(_day({9.0: 1.1680}), allow_short=False).fills == []
    late = _run(_day({13.0: 1.1720}))  # after the 4-hour entry window: no trade
    assert late.fills == []


def test_fx_rates_follow_live_prices():
    from test_autopilot import Harness

    h = Harness()
    assert h.autopilot.fx_rates()["JPY"] == Decimal("0.58")  # default until a price arrives
    set_quote(h.platform, "OANDA:USD_JPY", "149.99", "150.01")
    set_quote(h.platform, "OANDA:EUR_USD", "1.19999", "1.20001")
    rates = h.autopilot.fx_rates()
    assert rates["JPY"] == Decimal("0.5667") and rates["EUR"] == Decimal("102.0000")


def test_forex_universe_researches_after_the_new_york_close():
    from test_autopilot import Harness

    h = Harness()
    h.autopilot.update_config({"universe": ["OANDA:EUR_USD", "OANDA:XAU_USD"], "enabled": True}, "tester")
    h.autopilot.last_run_at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)  # Tuesday
    # 17:00 New York (21:00 UTC) plus 30 minutes, once every market in the universe has closed its day.
    assert h.autopilot.next_run_at() == datetime(2026, 9, 29, 21, 30, tzinfo=UTC)


def test_intraday_forex_research_runs_on_demo_data():
    from test_autopilot import Harness

    h = Harness()
    h.series = {}  # no broker history: synthetic data
    h.autopilot.update_config(
        {
            "universe": ["OANDA:EUR_USD", "OANDA:XAU_USD"],
            "interval_seconds": 900,
            "history_bars": 600,
            "capital": "1000000",  # an unlevered ounce of gold (~$4,000) must fit in one position
        },
        "tester",
    )
    run = h.autopilot.run_cycle()
    assert run.error is None and run.data_source == "synthetic demo data"
    labels = {row["signal"] for row in run.leaderboard}
    per_instrument = sum(1 for c in candidate_grid(900) if applies(c, EUR))
    assert run.trials == 2 * per_instrument and "orb" not in labels
    evaluated = [row for row in run.leaderboard if row["validation"].get("charges")]
    assert evaluated  # spread and financing are charged
