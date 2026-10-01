"""Leverage: off by default, capped per market, applied to sizing everywhere."""

from decimal import Decimal

from test_autopilot import Harness, nse

from jdquant.autopilot.research import (
    Candidate,
    ResearchConfig,
    leverage_for,
    market_leverage,
    strategy_parameters,
)
from jdquant.autopilot.strategy import volatility_scale
from jdquant.platform import fx_instrument

EUR, GOLD = fx_instrument("EUR_USD"), fx_instrument("XAU_USD")
RATES = {"INR": Decimal(1), "USD": Decimal(85)}


def _closes(daily_move: float, n: int = 61):
    price, out = 100.0, []
    for k in range(n):
        price *= 1 + (daily_move if k % 2 else -daily_move)
        out.append(Decimal(str(round(price, 6))))
    return out


def test_volatility_scale_uses_leverage_only_up_to_the_cap():
    quiet = _closes(0.003)  # about 5% a year on daily bars
    assert volatility_scale(quiet, Decimal("0.2"), 252) == Decimal(1)  # no leverage: capped at 1
    assert Decimal(3) < volatility_scale(quiet, Decimal("0.2"), 252, Decimal(10)) < Decimal(5)
    assert volatility_scale(quiet, Decimal("0.2"), 252, Decimal(2)) == Decimal(2)
    wild = _closes(0.05)
    assert volatility_scale(wild, Decimal("0.2"), 252, Decimal(10)) < Decimal(1)  # never levers a wild market


def test_each_market_has_its_own_cap_and_cash_markets_none():
    assert market_leverage(EUR, False) == (Decimal(30), "forex")
    assert market_leverage(GOLD, False) == (Decimal(20), "metal")
    assert market_leverage(nse("TREND-EQ"), False) == (Decimal(1), "cash")
    assert market_leverage(nse("TREND-EQ"), True) == (Decimal(5), "intraday")
    config = ResearchConfig(leverage=Decimal(50), fx_rates=RATES)
    assert leverage_for(EUR, config) == 30 and leverage_for(nse("TREND-EQ"), config) == 1
    params = strategy_parameters(Candidate("ewmac", (("fast", 8),)), Decimal(1000), config, GOLD)
    assert params["leverage"] == "20"
    default = strategy_parameters(Candidate("ewmac", (("fast", 8),)), Decimal(1000), ResearchConfig(), GOLD)
    assert default["leverage"] == "1"  # off unless chosen


def test_leverage_lets_an_ounce_of_gold_fit_a_small_budget():
    from jdquant.autopilot.research import research

    h = Harness()
    h.series = {}
    config = h.autopilot.config
    candles = {GOLD.instrument_id: h.autopilot._synthetic(GOLD)}
    small = ResearchConfig(capital=Decimal(300000), fx_rates=RATES, interval_seconds=config.interval_seconds)
    skipped = research(
        [GOLD], candles, small, candidates=[Candidate("ma_cross", (("fast", 10), ("slow", 30)))]
    )
    assert "one lot is worth" in skipped.skipped[GOLD.instrument_id]
    levered = ResearchConfig(
        capital=Decimal(300000), fx_rates=RATES, leverage=Decimal(5), interval_seconds=config.interval_seconds
    )
    ok = research([GOLD], candles, levered, candidates=[Candidate("ma_cross", (("fast", 10), ("slow", 30)))])
    assert GOLD.instrument_id not in ok.skipped and ok.evaluations


def test_leverage_setting_is_validated():
    import pytest

    from jdquant.core.errors import ValidationError

    h = Harness()
    h.autopilot.update_config({"leverage": "5"}, "tester")
    assert h.autopilot.config.leverage == Decimal(5)
    assert h.autopilot.config.research_config().leverage == Decimal(5)
    with pytest.raises(ValidationError):
        h.autopilot.update_config({"leverage": "0.5"}, "tester")
    with pytest.raises(ValidationError):
        h.autopilot.update_config({"leverage": "100"}, "tester")
