"""A backtest tearsheet: returns by month and year, and the worst drawdown episodes (after Vibe-Trading)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any


def tearsheet(equity: list[tuple[datetime, Decimal | float]], top: int = 5) -> dict[str, Any]:
    if len(equity) < 2:
        return {"monthly": [], "yearly": [], "drawdowns": []}
    points = [(t, float(v)) for t, v in equity]
    month_end: dict[tuple[int, int], float] = {}
    for t, v in points:
        month_end[(t.year, t.month)] = v
    months = sorted(month_end)
    monthly, previous = [], points[0][1]
    for key in months:
        value = month_end[key]
        monthly.append(
            {"year": key[0], "month": key[1], "return": value / previous - 1 if previous else None}
        )
        previous = value
    yearly, previous = [], points[0][1]
    for year in sorted({y for y, _ in months}):
        value = month_end[max(k for k in months if k[0] == year)]
        yearly.append({"year": year, "return": value / previous - 1 if previous else None})
        previous = value

    episodes, peak_t, peak_v, trough_t, trough_v = [], points[0][0], points[0][1], None, None
    for t, v in points[1:]:
        if v >= peak_v:
            if trough_v is not None and trough_v < peak_v:
                episodes.append(
                    {"peak": peak_t, "trough": trough_t, "recovered": t, "depth": trough_v / peak_v - 1}
                )
            peak_t, peak_v, trough_t, trough_v = t, v, None, None
        elif trough_v is None or v < trough_v:
            trough_t, trough_v = t, v
    if trough_v is not None and trough_v < peak_v:
        episodes.append(
            {"peak": peak_t, "trough": trough_t, "recovered": None, "depth": trough_v / peak_v - 1}
        )
    worst = sorted(episodes, key=lambda e: e["depth"])[:top]
    for e in worst:
        e["days_to_trough"] = (e["trough"] - e["peak"]).days
        e["days_to_recover"] = (e["recovered"] - e["trough"]).days if e["recovered"] else None
        for key in ("peak", "trough", "recovered"):
            e[key] = e[key].isoformat() if e[key] else None
    return {"monthly": monthly, "yearly": yearly, "drawdowns": worst}
