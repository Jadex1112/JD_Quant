"""The options payoff lab and the backtest tearsheet."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from jdquant.analytics.tearsheet import tearsheet
from jdquant.core.errors import ValidationError
from jdquant.intelligence.payoff import analyze, preset


def test_long_call_at_expiry_breakeven_and_unlimited_upside():
    legs = [{"kind": "CE", "side": "BUY", "strike": 100, "price": 5, "quantity": 50, "iv": 20, "days": 10}]
    r = analyze(legs, 100)
    assert r["breakevens"] == [pytest.approx(105, abs=0.05)]
    assert r["max_profit"] is None and r["max_loss"] == pytest.approx(-250)
    assert r["net_premium"] == -250  # a debit
    assert r["greeks"]["delta"] > 0 and r["greeks"]["theta"] < 0
    assert 0 < r["probability_of_profit"] < 0.5


def test_iron_condor_is_bounded_both_ways():
    legs = preset("iron_condor", 22000, 50, lots=75, days=7, iv=0.14)
    assert [leg["side"] for leg in legs] == ["BUY", "SELL", "SELL", "BUY"]
    r = analyze(legs, 22000)
    assert r["net_premium"] > 0  # a credit
    assert r["max_profit"] == pytest.approx(r["net_premium"], rel=1e-6)
    assert r["max_loss"] is not None and r["max_loss"] < 0
    low, high = r["breakevens"]
    assert len(r["breakevens"]) == 2 and low < 22000 < high
    # about the chance of ending between the breakevens: 2 * N(172 / 426) - 1 at 14% volatility over 7 days
    one_sd = 22000 * 0.14 * (7 / 365) ** 0.5
    z = (high - 22000) / one_sd
    normal = math.erf(z / math.sqrt(2))
    assert r["probability_of_profit"] == pytest.approx(normal, abs=0.05)
    today, expiry = r["curve"][100]["today"], r["curve"][100]["expiry"]
    assert today < expiry  # time value still to decay at the money
    grid = r["scenarios"][0]
    assert grid["days_passed"] == 0 and len(grid["pnl"]) == len(r["scenario_moves_pct"])


def test_bad_legs_are_rejected():
    with pytest.raises(ValidationError):
        analyze([{"kind": "XX", "side": "BUY", "price": 1, "quantity": 1}], 100)
    with pytest.raises(ValidationError):
        preset("lottery", 100, 1, lots=1, days=1, iv=0.2)


def test_tearsheet_months_years_and_drawdowns():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    values = [100, 110, 99, 105, 121, 90, 95]  # month ends; a 10% dip, then a 25.6% one never recovered
    curve = [(start + timedelta(days=31 * i), Decimal(v)) for i, v in enumerate(values)]
    t = tearsheet(curve)
    assert t["monthly"][1]["return"] == pytest.approx(0.10)
    assert t["yearly"][0]["year"] == 2025
    worst = t["drawdowns"][0]
    assert worst["depth"] == pytest.approx(90 / 121 - 1) and worst["recovered"] is None
    assert t["drawdowns"][1]["depth"] == pytest.approx(-0.10) and t["drawdowns"][1]["recovered"]


def test_payoff_api(app_ctx):
    client = app_ctx[0]
    body = {"name": "long_straddle", "spot": 22000, "step": 50}
    legs = client.post("/api/v1/intelligence/payoff/preset", json=body).json()["legs"]
    r = client.post("/api/v1/intelligence/payoff", json={"legs": legs, "spot": 22000}).json()
    assert len(r["breakevens"]) == 2 and r["max_profit"] is None
