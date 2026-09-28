"""Options payoff lab: what a multi-leg position makes or loses, at expiry and before it.

For legs of calls, puts, futures and the underlying, this gives the profit and loss across prices at expiry
(intrinsic value) and today (Black-Scholes with each leg's implied volatility), breakevens, the maximum
profit and loss in the range (flagged unlimited when the payoff keeps sloping at the edge), net Greeks, a
grid of scenarios (price move x volatility change x days passed), and the probability of finishing in
profit if prices follow the lognormal distribution the implied volatility describes. That probability is
only as good as that assumption: real markets have fatter tails. Exchange margin (SPAN) is not computed.
"""

from __future__ import annotations

import math
from typing import Any

from jdquant.core.errors import ValidationError
from jdquant.intelligence.options import RISK_FREE, bs_price, greeks

KINDS = ("CE", "PE", "FUT", "SPOT")


def _validate(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    problems, out = [], []
    if not legs or len(legs) > 12:
        raise ValidationError("LEGS_INVALID", [{"field": "legs", "message": "1 to 12 legs"}])
    for i, leg in enumerate(legs):
        kind, side = str(leg.get("kind", "")).upper(), str(leg.get("side", "")).upper()
        try:
            quantity = float(leg["quantity"])
            price = float(leg["price"])
            strike = float(leg.get("strike") or 0)
            iv = float(leg.get("iv") or 0) / (100 if float(leg.get("iv") or 0) > 3 else 1)  # 14 or 0.14
            days = float(leg.get("days", 0))
        except (KeyError, TypeError, ValueError):
            problems.append(
                {"field": f"legs[{i}]", "message": "quantity, price, strike, iv and days must be numbers"}
            )
            continue
        if kind not in KINDS or side not in ("BUY", "SELL"):
            problems.append({"field": f"legs[{i}]", "message": "kind CE/PE/FUT/SPOT and side BUY/SELL"})
        if kind in ("CE", "PE") and (strike <= 0 or days < 0):
            problems.append({"field": f"legs[{i}]", "message": "options need a strike and days to expiry"})
        if quantity <= 0 or price < 0:
            problems.append({"field": f"legs[{i}]", "message": "quantity above 0, price 0 or more"})
        out.append(
            {
                "kind": kind,
                "side": side,
                "sign": 1 if side == "BUY" else -1,
                "quantity": quantity,
                "price": price,
                "strike": strike,
                "iv": iv,
                "days": days,
            }
        )
    if problems:
        raise ValidationError("LEGS_INVALID", problems)
    return out


def _value(leg: dict, spot: float, days_left: float, iv_shift: float, r: float) -> float:
    """One unit of the leg's instrument at this spot and time (futures and spot are worth the price)."""
    if leg["kind"] in ("FUT", "SPOT"):
        return spot
    if days_left <= 0:
        return max(0.0, spot - leg["strike"]) if leg["kind"] == "CE" else max(0.0, leg["strike"] - spot)
    vol = max(0.01, leg["iv"] + iv_shift) if leg["iv"] else 0.0
    return bs_price(spot, leg["strike"], days_left / 365, vol, leg["kind"], r)


def pnl(
    legs: list[dict], spot: float, *, days_passed: float, iv_shift: float = 0.0, r: float = RISK_FREE
) -> float:
    total = 0.0
    for leg in legs:
        days_left = max(0.0, leg["days"] - days_passed) if leg["kind"] in ("CE", "PE") else 0.0
        total += leg["sign"] * leg["quantity"] * (_value(leg, spot, days_left, iv_shift, r) - leg["price"])
    return total


def analyze(
    legs_in: list[dict[str, Any]], spot: float, *, range_pct: float = 20.0, r: float = RISK_FREE
) -> dict:
    if spot <= 0:
        raise ValidationError("SPOT_INVALID", [{"field": "spot", "message": "must be above 0"}])
    legs = _validate(legs_in)
    expiry = max((leg["days"] for leg in legs if leg["kind"] in ("CE", "PE")), default=0.0)
    low, high = spot * (1 - range_pct / 100), spot * (1 + range_pct / 100)
    steps = 200
    prices = [low + (high - low) * i / steps for i in range(steps + 1)]
    at_expiry = [pnl(legs, p, days_passed=expiry, r=r) for p in prices]
    today = [pnl(legs, p, days_passed=0, r=r) for p in prices]

    breakevens = []
    for (p0, v0), (p1, v1) in zip(
        zip(prices, at_expiry, strict=True), zip(prices[1:], at_expiry[1:], strict=True), strict=False
    ):
        if v0 == 0:
            breakevens.append(p0)
        elif v0 * v1 < 0:
            breakevens.append(p0 + (p1 - p0) * (-v0) / (v1 - v0))
    slope_low = at_expiry[1] - at_expiry[0]
    slope_high = at_expiry[-1] - at_expiry[-2]
    max_profit, max_loss = max(at_expiry), min(at_expiry)

    net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in legs:
        scale = leg["sign"] * leg["quantity"]
        if leg["kind"] in ("FUT", "SPOT"):
            net["delta"] += scale
            continue
        g = greeks(spot, leg["strike"], leg["days"] / 365, leg["iv"] or 0.0, leg["kind"], r)
        for key in net:
            net[key] += scale * (g.get(key) or 0.0)
    premium = sum(
        -leg["sign"] * leg["quantity"] * leg["price"] for leg in legs if leg["kind"] in ("CE", "PE")
    )

    moves = (-10, -5, -2, 0, 2, 5, 10)
    iv_shifts = (-0.05, 0.0, 0.05)
    days = sorted({0.0, round(expiry / 2, 2), expiry}) if expiry else [0.0]
    scenarios = [
        {
            "days_passed": d,
            "iv_change_pts": round(s * 100),
            "pnl": [pnl(legs, spot * (1 + m / 100), days_passed=d, iv_shift=s, r=r) for m in moves],
        }
        for d in days
        for s in iv_shifts
    ]

    ivs = [leg["iv"] for leg in legs if leg["kind"] in ("CE", "PE") and leg["iv"]]
    probability = None
    if expiry and ivs:
        sigma = sum(ivs) / len(ivs) * math.sqrt(expiry / 365)
        mu = math.log(spot) + (r - 0.5 * (sigma**2 / (expiry / 365))) * expiry / 365

        def cdf(price: float) -> float:
            return 0.5 * (1 + math.erf((math.log(price) - mu) / (sigma * math.sqrt(2))))

        edges = [0.0] + [(a + b) / 2 for a, b in zip(prices, prices[1:], strict=False)] + [float("inf")]
        probability = 0.0
        for i, value in enumerate(at_expiry):
            if value > 0:
                lo = cdf(edges[i]) if edges[i] > 0 else 0.0
                hi = cdf(edges[i + 1]) if math.isfinite(edges[i + 1]) else 1.0
                probability += hi - lo
    return {
        "spot": spot,
        "days_to_expiry": expiry,
        "net_premium": premium,  # positive: credit received; negative: debit paid
        "breakevens": [round(b, 4) for b in breakevens],
        "max_profit": None if slope_high > 1e-9 or slope_low < -1e-9 else max_profit,
        "max_loss": None if slope_high < -1e-9 or slope_low > 1e-9 else max_loss,
        "max_profit_in_range": max_profit,
        "max_loss_in_range": min(at_expiry),
        "greeks": net,
        "probability_of_profit": probability,
        "curve": [
            {"price": p, "expiry": e, "today": t} for p, e, t in zip(prices, at_expiry, today, strict=True)
        ],
        "scenario_moves_pct": list(moves),
        "scenarios": scenarios,
        "notes": [
            "Probability of profit assumes lognormal prices at the legs' implied volatility; "
            "real tails are fatter.",
            "Exchange margin (SPAN plus exposure) is not computed; ask your broker's margin calculator.",
        ],
    }


def preset(name: str, spot: float, step: float, *, lots: float, days: float, iv: float, r: float = RISK_FREE):
    """Common structures priced at the given volatility, for a quick start."""
    atm = round(spot / step) * step

    def leg(kind, side, strike):
        price = bs_price(spot, strike, days / 365, iv, kind, r)
        return {
            "kind": kind,
            "side": side,
            "strike": strike,
            "price": round(price, 2),
            "quantity": lots,
            "iv": iv * 100,
            "days": days,
        }

    table = {
        "long_straddle": [leg("CE", "BUY", atm), leg("PE", "BUY", atm)],
        "short_strangle": [leg("CE", "SELL", atm + 2 * step), leg("PE", "SELL", atm - 2 * step)],
        "bull_call_spread": [leg("CE", "BUY", atm), leg("CE", "SELL", atm + 2 * step)],
        "bear_put_spread": [leg("PE", "BUY", atm), leg("PE", "SELL", atm - 2 * step)],
        "iron_condor": [
            leg("PE", "BUY", atm - 4 * step),
            leg("PE", "SELL", atm - 2 * step),
            leg("CE", "SELL", atm + 2 * step),
            leg("CE", "BUY", atm + 4 * step),
        ],
        "covered_call": [
            {"kind": "SPOT", "side": "BUY", "strike": 0, "price": spot, "quantity": lots, "iv": 0, "days": 0},
            leg("CE", "SELL", atm + 2 * step),
        ],
    }
    if name not in table:
        raise ValidationError("PRESET_UNKNOWN", [{"field": "preset", "message": f"one of {sorted(table)}"}])
    return table[name]
