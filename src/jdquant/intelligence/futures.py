"""Futures intelligence: price vs open interest, basis to spot, and the spread between expiries.

The price/open-interest combination is the standard reading of futures positioning:

| Price | Open interest | Reading |
|---|---|---|
| up | up | long build-up (new buyers) |
| up | down | short covering (sellers closing) |
| down | up | short build-up (new sellers) |
| down | down | long unwinding (buyers closing) |

It says who is likely opening or closing positions, not which way price goes next.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

QUADRANTS = {
    (1, 1): ("LONG_BUILDUP", "Long build-up: price and open interest rising together (new longs)"),
    (1, -1): ("SHORT_COVERING", "Short covering: price rising while open interest falls (shorts closing)"),
    (-1, 1): ("SHORT_BUILDUP", "Short build-up: price falling while open interest rises (new shorts)"),
    (-1, -1): ("LONG_UNWINDING", "Long unwinding: price and open interest falling (longs closing)"),
}


@dataclass
class FuturesObservation:
    instrument_id: str
    price: float
    open_interest: float | None
    at: datetime
    expiry: datetime | None = None
    reference_price: float | None = None  # previous close, or the first price of the session
    reference_oi: float | None = None
    volume: float | None = None


def classify(
    obs: FuturesObservation, *, min_price_pct: float = 0.1, min_oi_pct: float = 0.5
) -> dict[str, Any]:
    """Quadrant of price change vs open-interest change, ignoring changes too small to mean anything."""
    if obs.reference_price is None or obs.open_interest is None or not obs.reference_oi:
        return {"state": None, "reading": "Needs a reference price and open interest"}
    price_pct = (obs.price - obs.reference_price) / obs.reference_price * 100
    oi_pct = (obs.open_interest - obs.reference_oi) / obs.reference_oi * 100
    p = 1 if price_pct >= min_price_pct else -1 if price_pct <= -min_price_pct else 0
    o = 1 if oi_pct >= min_oi_pct else -1 if oi_pct <= -min_oi_pct else 0
    if p == 0 or o == 0:
        state, reading = "NEUTRAL", "Price or open interest barely changed"
    else:
        state, reading = QUADRANTS[(p, o)]
    return {
        "state": state,
        "reading": reading,
        "price_change_pct": round(price_pct, 3),
        "oi_change_pct": round(oi_pct, 3),
        "oi_change": obs.open_interest - obs.reference_oi,
    }


def basis(future_price: float, spot: float, expiry: datetime | None, now: datetime) -> dict[str, Any]:
    """Premium (or discount) to spot, and its annualized rate (the implied cost of carry)."""
    diff = future_price - spot
    pct = diff / spot * 100 if spot else None
    days = (expiry - now).total_seconds() / 86400 if expiry else None
    annual = pct * 365 / days if pct is not None and days and days > 0.5 else None
    return {
        "basis": diff,
        "basis_pct": pct,
        "days_to_expiry": None if days is None else round(days, 2),
        "annualized_pct": annual,
        "state": "PREMIUM" if diff > 0 else "DISCOUNT" if diff < 0 else "FLAT",
    }


def calendar_spread(near: FuturesObservation, far: FuturesObservation) -> dict[str, Any]:
    spread = far.price - near.price
    return {
        "near": near.instrument_id,
        "far": far.instrument_id,
        "spread": spread,
        "spread_pct": spread / near.price * 100 if near.price else None,
        "state": "CONTANGO" if spread > 0 else "BACKWARDATION" if spread < 0 else "FLAT",
    }
