"""Options pricing, IV, Greeks and chain analytics; futures positioning and basis."""

import copy
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from jdquant.intelligence.futures import FuturesObservation, basis, calendar_spread, classify
from jdquant.intelligence.options import (
    OptionChain,
    OptionRow,
    analyze,
    bs_price,
    greeks,
    implied_vol,
    max_pain,
    shocks,
    simulated_chain,
)

NOW = datetime(2026, 9, 28, 5, 0, tzinfo=UTC)


def test_put_call_parity_and_iv_round_trip():
    s, k, t, v, r = 26_000.0, 26_200.0, 30 / 365, 0.14, 0.065
    call, put = bs_price(s, k, t, v, "CE", r), bs_price(s, k, t, v, "PE", r)
    assert call - put == pytest.approx(s - k * math.exp(-r * t), rel=1e-9)
    assert implied_vol(call, s, k, t, "CE", r) == pytest.approx(v, abs=1e-5)
    assert implied_vol(0.0001, s, 20_000, t, "CE", r) is None  # below intrinsic: no IV


def test_greek_signs():
    g_call = greeks(26_000, 26_000, 7 / 365, 0.13, "CE")
    g_put = greeks(26_000, 26_000, 7 / 365, 0.13, "PE")
    assert 0.4 < g_call["delta"] < 0.6 and -0.6 < g_put["delta"] < -0.4
    assert g_call["gamma"] > 0 and g_call["vega"] > 0 and g_call["theta"] < 0 and g_put["theta"] < 0


def test_max_pain_is_where_holders_lose_most():
    rows = [
        OptionRow(100, "CE", oi=10),
        OptionRow(100, "PE", oi=1000),
        OptionRow(110, "CE", oi=500),
        OptionRow(110, "PE", oi=500),
        OptionRow(120, "CE", oi=1000),
        OptionRow(120, "PE", oi=10),
    ]
    chain = OptionChain("TEST", date(2026, 10, 1), 110.0, NOW, rows, "TEST")
    assert max_pain(chain) == 110


def test_chain_analysis_reports_observations_not_advice():
    chain = simulated_chain("NIFTY", 26_240.0, NOW, seed=7)
    result = analyze(chain)
    assert result["simulated"] is True and result["atm"] == 26_250
    assert result["pcr_oi"] and result["max_pain"] and result["atm_iv"] > 0
    assert result["resistance"][0]["strike"] > 26_240 and result["support"][0]["strike"] < 26_240
    assert any("resistance area" in o for o in result["observations"])
    assert not any(word in " ".join(result["observations"]).upper() for word in ("BUY ", "SELL "))
    row = next(r for r in result["rows"] if r["strike"] == 26_250)
    assert row["call"]["delta"] > 0 > row["put"]["delta"]


def test_oi_and_iv_shocks_between_snapshots():
    before = simulated_chain("NIFTY", 26_240.0, NOW, seed=1)
    after = copy.deepcopy(before)
    after.at = NOW + timedelta(minutes=5)
    target = after.row(26_500, "CE")
    target.oi *= 2
    for r in after.rows:
        r.iv = None if r.iv is None else r.iv + 0.03
        r.delta = None
    found = shocks(after, before)
    assert any(s["kind"] == "OI_SHOCK" and s["strike"] == 26_500 for s in found)
    assert any(s["kind"] == "IV_SHOCK" for s in found)
    changes = analyze(after, before)["changes"]
    assert changes["largest_oi_moves"][0]["strike"] == 26_500


@pytest.mark.parametrize(
    "price,oi,state",
    [
        (105, 1100, "LONG_BUILDUP"),
        (105, 900, "SHORT_COVERING"),
        (95, 1100, "SHORT_BUILDUP"),
        (95, 900, "LONG_UNWINDING"),
        (100.01, 1100, "NEUTRAL"),
    ],
)
def test_futures_quadrants(price, oi, state):
    obs = FuturesObservation("NSE:NIFTY26OCTFUT", price, oi, NOW, reference_price=100, reference_oi=1000)
    assert classify(obs)["state"] == state


def test_basis_and_calendar_spread():
    b = basis(26_300, 26_240, NOW + timedelta(days=30), NOW)
    assert b["state"] == "PREMIUM" and b["annualized_pct"] == pytest.approx(
        60 / 26_240 * 100 * 365 / 30, rel=1e-6
    )
    near = FuturesObservation("NEAR", 26_300, None, NOW)
    far = FuturesObservation("FAR", 26_420, None, NOW)
    assert calendar_spread(near, far)["state"] == "CONTANGO"
