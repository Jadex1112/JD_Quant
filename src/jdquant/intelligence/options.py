"""Options intelligence: pricing, implied volatility, Greeks and option-chain analytics.

The chain analytics report what the chain shows (open interest and its change, volume, IV, the
put-call ratio, max pain, where call and put open interest concentrate) and how it changed between
snapshots. They describe positioning; they are not trade recommendations.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

RISK_FREE = 0.065  # Indian short-term rate, used where the chain does not supply IV
IST_CLOSE = time(10, 0)  # 15:30 IST in UTC


# ---- Black-Scholes --------------------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(
    spot: float, strike: float, years: float, vol: float, kind: str, r: float = RISK_FREE, q: float = 0.0
):
    """European option value (Black-Scholes-Merton with continuous yield q)."""
    if years <= 0 or vol <= 0:
        intrinsic = spot - strike if kind == "CE" else strike - spot
        return max(0.0, intrinsic)
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * years) / sq
    d2 = d1 - sq
    if kind == "CE":
        return spot * math.exp(-q * years) * _norm_cdf(d1) - strike * math.exp(-r * years) * _norm_cdf(d2)
    return strike * math.exp(-r * years) * _norm_cdf(-d2) - spot * math.exp(-q * years) * _norm_cdf(-d1)


def greeks(
    spot: float, strike: float, years: float, vol: float, kind: str, r: float = RISK_FREE, q: float = 0.0
):
    """Delta, gamma, theta per calendar day, vega per volatility point, rho per point of rate."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        itm = (spot > strike) if kind == "CE" else (spot < strike)
        return {
            "delta": (1.0 if itm else 0.0) * (1 if kind == "CE" else -1),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
        }
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * years) / sq
    d2 = d1 - sq
    disc_q, disc_r = math.exp(-q * years), math.exp(-r * years)
    gamma = disc_q * _norm_pdf(d1) / (spot * sq)
    vega = spot * disc_q * _norm_pdf(d1) * math.sqrt(years) / 100
    if kind == "CE":
        delta = disc_q * _norm_cdf(d1)
        theta = (
            -spot * disc_q * _norm_pdf(d1) * vol / (2 * math.sqrt(years))
            - r * strike * disc_r * _norm_cdf(d2)
            + q * spot * disc_q * _norm_cdf(d1)
        )
        rho = strike * years * disc_r * _norm_cdf(d2) / 100
    else:
        delta = disc_q * (_norm_cdf(d1) - 1)
        theta = (
            -spot * disc_q * _norm_pdf(d1) * vol / (2 * math.sqrt(years))
            + r * strike * disc_r * _norm_cdf(-d2)
            - q * spot * disc_q * _norm_cdf(-d1)
        )
        rho = -strike * years * disc_r * _norm_cdf(-d2) / 100
    return {"delta": delta, "gamma": gamma, "theta": theta / 365, "vega": vega, "rho": rho}


def implied_vol(price: float, spot: float, strike: float, years: float, kind: str, r: float = RISK_FREE):
    """Volatility that reproduces the price (bisection); None outside the no-arbitrage bounds."""
    if price <= 0 or years <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = max(
        0.0,
        (spot - strike * math.exp(-r * years)) if kind == "CE" else (strike * math.exp(-r * years) - spot),
    )
    if price < intrinsic - 1e-9:
        return None
    low, high = 1e-4, 5.0
    if bs_price(spot, strike, years, high, kind, r) < price:
        return None
    for _ in range(100):
        mid = (low + high) / 2
        if bs_price(spot, strike, years, mid, kind, r) > price:
            high = mid
        else:
            low = mid
        if high - low < 1e-6:
            break
    return (low + high) / 2


def years_to(expiry: date, now: datetime) -> float:
    """Time to expiry in years, to the 15:30 IST close of the expiry day."""
    close = datetime.combine(expiry, IST_CLOSE, UTC)
    return max(0.0, (close - now).total_seconds() / (365 * 86400))


# ---- the chain ------------------------------------------------------------------------------------------


@dataclass
class OptionRow:
    strike: float
    kind: str  # CE or PE
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    oi: float | None = None
    oi_change: float | None = None  # vs the previous session
    volume: float | None = None
    iv: float | None = None  # decimal (0.12 = 12%)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    symbol: str = ""


@dataclass
class OptionChain:
    underlying: str
    expiry: date
    spot: float
    at: datetime
    rows: list[OptionRow]
    source: str
    expiries: list[date] = field(default_factory=list)
    lot_size: float | None = None
    simulated: bool = False

    def strikes(self) -> list[float]:
        return sorted({r.strike for r in self.rows})

    def row(self, strike: float, kind: str) -> OptionRow | None:
        return next((r for r in self.rows if r.strike == strike and r.kind == kind), None)


def complete(chain: OptionChain) -> OptionChain:
    """Fill in IV and Greeks the source did not provide, from mid (or last) prices."""
    years = years_to(chain.expiry, chain.at)
    for row in chain.rows:
        price = None
        if row.bid and row.ask and row.ask >= row.bid > 0:
            price = (row.bid + row.ask) / 2
        elif row.ltp:
            price = row.ltp
        if row.iv is None and price:
            row.iv = implied_vol(price, chain.spot, row.strike, years, row.kind)
        if row.iv and row.delta is None:
            g = greeks(chain.spot, row.strike, years, row.iv, row.kind)
            row.delta, row.gamma, row.theta, row.vega = g["delta"], g["gamma"], g["theta"], g["vega"]
    return chain


def max_pain(chain: OptionChain) -> float | None:
    """The expiry price at which option holders' total intrinsic value is smallest."""
    strikes = chain.strikes()
    if not strikes:
        return None
    calls = {r.strike: r.oi or 0 for r in chain.rows if r.kind == "CE"}
    puts = {r.strike: r.oi or 0 for r in chain.rows if r.kind == "PE"}

    def payout(settle: float) -> float:
        return sum(oi * max(0.0, settle - k) for k, oi in calls.items()) + sum(
            oi * max(0.0, k - settle) for k, oi in puts.items()
        )

    return min(strikes, key=payout)


def analyze(chain: OptionChain, previous: OptionChain | None = None) -> dict[str, Any]:
    """Positioning summary with the observations behind every statement."""
    complete(chain)
    strikes = chain.strikes()
    atm = min(strikes, key=lambda k: abs(k - chain.spot)) if strikes else None
    calls = [r for r in chain.rows if r.kind == "CE"]
    puts = [r for r in chain.rows if r.kind == "PE"]
    call_oi, put_oi = sum(r.oi or 0 for r in calls), sum(r.oi or 0 for r in puts)
    call_vol, put_vol = sum(r.volume or 0 for r in calls), sum(r.volume or 0 for r in puts)
    call_chg, put_chg = sum(r.oi_change or 0 for r in calls), sum(r.oi_change or 0 for r in puts)
    pcr_oi = put_oi / call_oi if call_oi else None
    pcr_vol = put_vol / call_vol if call_vol else None
    top_calls = sorted(calls, key=lambda r: r.oi or 0, reverse=True)[:3]
    top_puts = sorted(puts, key=lambda r: r.oi or 0, reverse=True)[:3]
    atm_call, atm_put = (chain.row(atm, "CE"), chain.row(atm, "PE")) if atm is not None else (None, None)
    atm_ivs = [r.iv for r in (atm_call, atm_put) if r is not None and r.iv]
    atm_iv = sum(atm_ivs) / len(atm_ivs) if atm_ivs else None
    years = years_to(chain.expiry, chain.at)
    expected_move = chain.spot * atm_iv * math.sqrt(years) if atm_iv and years else None
    skew = _skew(calls, puts)
    liquidity = []
    for r in chain.rows:
        if r.bid and r.ask and r.ask > r.bid:
            mid = (r.bid + r.ask) / 2
            liquidity.append(
                {
                    "strike": r.strike,
                    "kind": r.kind,
                    "spread_pct": round((r.ask - r.bid) / mid * 100, 2),
                    "volume": r.volume,
                }
            )
    observations = []
    if top_calls and top_calls[0].oi:
        observations.append(
            f"Largest call open interest at {top_calls[0].strike:g} ({_lakh(top_calls[0].oi)}): "
            "call writers' resistance area"
        )
    if top_puts and top_puts[0].oi:
        observations.append(
            f"Largest put open interest at {top_puts[0].strike:g} ({_lakh(top_puts[0].oi)}): "
            "put writers' support area"
        )
    if call_chg or put_chg:
        observations.append(f"Call OI {_signed_lakh(call_chg)}, put OI {_signed_lakh(put_chg)} today")
    if pcr_oi is not None:
        observations.append(
            f"Put-call ratio {pcr_oi:.2f} by open interest"
            + (f", {pcr_vol:.2f} by volume" if pcr_vol is not None else "")
        )
    if atm_iv:
        observations.append(
            f"ATM implied volatility {atm_iv * 100:.1f}%; the market prices a "
            f"±{expected_move:,.0f} move to expiry"
            if expected_move
            else f"ATM IV {atm_iv * 100:.1f}%"
        )
    changes = _changes(chain, previous) if previous is not None else None
    return {
        "underlying": chain.underlying,
        "expiry": chain.expiry.isoformat(),
        "expiries": [d.isoformat() for d in chain.expiries],
        "spot": chain.spot,
        "at": chain.at.isoformat(),
        "source": chain.source,
        "simulated": chain.simulated,
        "atm": atm,
        "pcr_oi": pcr_oi,
        "pcr_volume": pcr_vol,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_oi_change": call_chg,
        "put_oi_change": put_chg,
        "max_pain": max_pain(chain),
        "atm_iv": atm_iv,
        "expected_move": expected_move,
        "days_to_expiry": round(years * 365, 2),
        "skew": skew,
        "resistance": [{"strike": r.strike, "oi": r.oi, "oi_change": r.oi_change} for r in top_calls],
        "support": [{"strike": r.strike, "oi": r.oi, "oi_change": r.oi_change} for r in top_puts],
        "liquidity": sorted(liquidity, key=lambda x: x["spread_pct"])[:10],
        "observations": observations,
        "changes": changes,
        "rows": [
            {
                "strike": k,
                "call": _row_dict(chain.row(k, "CE")),
                "put": _row_dict(chain.row(k, "PE")),
            }
            for k in strikes
        ],
    }


def _row_dict(r: OptionRow | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "ltp": r.ltp,
        "bid": r.bid,
        "ask": r.ask,
        "oi": r.oi,
        "oi_change": r.oi_change,
        "volume": r.volume,
        "iv": None if r.iv is None else round(r.iv * 100, 2),
        "delta": None if r.delta is None else round(r.delta, 3),
        "gamma": None if r.gamma is None else round(r.gamma, 5),
        "theta": None if r.theta is None else round(r.theta, 2),
        "vega": None if r.vega is None else round(r.vega, 2),
        "symbol": r.symbol,
    }


def _skew(calls: list[OptionRow], puts: list[OptionRow]) -> dict[str, Any] | None:
    """25-delta risk reversal: put IV minus call IV at |delta| near 0.25 (positive: puts richer)."""

    def near(rows, target):
        rows = [r for r in rows if r.delta is not None and r.iv]
        return min(rows, key=lambda r: abs(abs(r.delta) - target), default=None)

    c, p = near(calls, 0.25), near(puts, 0.25)
    if c is None or p is None:
        return None
    return {"put_25d_iv": p.iv, "call_25d_iv": c.iv, "risk_reversal": p.iv - c.iv}


def _changes(chain: OptionChain, previous: OptionChain) -> dict[str, Any]:
    """What moved between two snapshots: OI at each strike, ATM IV, PCR."""
    moves = []
    for r in chain.rows:
        before = previous.row(r.strike, r.kind)
        if before is None or before.oi is None or r.oi is None:
            continue
        diff = r.oi - before.oi
        if diff:
            moves.append(
                {
                    "strike": r.strike,
                    "kind": r.kind,
                    "oi_before": before.oi,
                    "oi_now": r.oi,
                    "change": diff,
                    "change_pct": diff / before.oi * 100 if before.oi else None,
                }
            )
    moves.sort(key=lambda m: abs(m["change"]), reverse=True)
    prev = analyze_light(previous)
    now = analyze_light(chain)
    return {
        "since": previous.at.isoformat(),
        "largest_oi_moves": moves[:8],
        "atm_iv_change": (now["atm_iv"] - prev["atm_iv"]) if now["atm_iv"] and prev["atm_iv"] else None,
        "pcr_change": (now["pcr"] - prev["pcr"]) if now["pcr"] and prev["pcr"] else None,
        "spot_change": chain.spot - previous.spot,
    }


def analyze_light(chain: OptionChain) -> dict[str, Any]:
    complete(chain)
    strikes = chain.strikes()
    atm = min(strikes, key=lambda k: abs(k - chain.spot)) if strikes else None
    ivs = [r.iv for r in chain.rows if r.strike == atm and r.iv]
    call_oi = sum(r.oi or 0 for r in chain.rows if r.kind == "CE")
    put_oi = sum(r.oi or 0 for r in chain.rows if r.kind == "PE")
    return {"atm_iv": sum(ivs) / len(ivs) if ivs else None, "pcr": put_oi / call_oi if call_oi else None}


def shocks(
    chain: OptionChain,
    previous: OptionChain,
    *,
    oi_pct: float = 25.0,
    oi_share: float = 0.05,
    iv_points: float = 2.0,
) -> list[dict[str, Any]]:
    """Sharp changes worth an event: OI at a strike, ATM IV, and PCR extremes."""
    out = []
    total_oi = sum(r.oi or 0 for r in chain.rows) or 1
    for r in chain.rows:
        before = previous.row(r.strike, r.kind)
        if before is None or not before.oi or r.oi is None:
            continue
        diff = r.oi - before.oi
        if (
            abs(diff) / before.oi * 100 >= oi_pct
            and abs(diff) >= oi_share * total_oi / max(1, len(chain.rows)) * 10
        ):
            out.append(
                {
                    "kind": "OI_SHOCK",
                    "strike": r.strike,
                    "option": r.kind,
                    "change": diff,
                    "change_pct": diff / before.oi * 100,
                    "oi": r.oi,
                }
            )
    now, prev = analyze_light(chain), analyze_light(previous)
    if now["atm_iv"] and prev["atm_iv"] and abs(now["atm_iv"] - prev["atm_iv"]) * 100 >= iv_points:
        out.append(
            {
                "kind": "IV_SHOCK",
                "atm_iv": now["atm_iv"],
                "previous": prev["atm_iv"],
                "change_points": (now["atm_iv"] - prev["atm_iv"]) * 100,
            }
        )
    if now["pcr"] is not None and (now["pcr"] >= 1.5 or now["pcr"] <= 0.6):
        out.append({"kind": "PCR_EXTREME", "pcr": now["pcr"]})
    return out


def _lakh(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e7:
        return f"{value / 1e7:.2f} Cr"
    if abs(value) >= 1e5:
        return f"{value / 1e5:.1f}L"
    return f"{value:,.0f}"


def _signed_lakh(value: float) -> str:
    return ("+" if value >= 0 else "-") + _lakh(abs(value))


# ---- simulated chain (demo) -----------------------------------------------------------------------------


def simulated_chain(
    underlying: str,
    spot: float,
    now: datetime,
    *,
    step: float | None = None,
    strikes_each_side: int = 12,
    seed: int | None = None,
) -> OptionChain:
    """A plausible chain for demos when no broker supplies one; always marked simulated."""
    rng = random.Random(seed if seed is not None else hash((underlying, now.date())) & 0xFFFF)
    step = step or (50.0 if spot > 10_000 else 100.0 if spot > 40_000 else max(1.0, round(spot * 0.01)))
    atm = round(spot / step) * step
    days_ahead = (3 - now.weekday()) % 7 or 7  # next Thursday
    expiry = (now + timedelta(days=days_ahead)).date()
    years = years_to(expiry, now)
    rows = []
    for k in range(-strikes_each_side, strikes_each_side + 1):
        strike = atm + k * step
        moneyness = (strike - spot) / spot
        for kind in ("CE", "PE"):
            iv = 0.13 + 0.8 * moneyness * moneyness - (0.05 * moneyness if kind == "PE" else 0.0)
            price = bs_price(spot, strike, years, iv, kind)
            wall = 1.0 + (3.0 if (kind == "CE" and k in (4, 8)) or (kind == "PE" and k in (-4, -8)) else 0.0)
            oi = max(0.0, rng.gauss(1, 0.2)) * 1e6 * math.exp(-abs(k) / 8) * wall
            rows.append(
                OptionRow(
                    strike,
                    kind,
                    round(price, 2),
                    round(price * 0.995, 2),
                    round(price * 1.005 + 0.05, 2),
                    round(oi),
                    round(oi * rng.uniform(-0.2, 0.3)),
                    round(oi * rng.uniform(0.5, 3)),
                    symbol=f"{underlying}{expiry:%y%b}{strike:g}{kind}".upper(),
                )
            )
    return complete(OptionChain(underlying, expiry, spot, now, rows, "SIMULATED", [expiry], simulated=True))
