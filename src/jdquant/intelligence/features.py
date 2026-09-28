"""Machine-readable market features for strategies, built from the intelligence engines.

The same function serves live trading (from the service's engines) and replay backtests (from a
replay's fresh engines), so a strategy sees identical inputs in both.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from jdquant.intelligence.common import tick_size
from jdquant.intelligence.orderbook import ASK, BID


def build(instrument, *, flow, orderbook, events, book, now, window_seconds: float = 120) -> dict[str, Any]:
    iid = instrument.instrument_id
    snap = flow.snapshot(iid) or {}
    price = snap.get("last_price") or (book.mid if book is not None else None)
    if price is None:
        return {}
    tick = tick_size(instrument)

    def wall(side):
        w = orderbook.nearest(iid, side, price)
        if w is None:
            return None
        return {
            "price": w.price,
            "quantity": w.current_quantity,
            "orders": w.current_orders,
            "distance_ticks": abs(price - w.price) / tick,
            "size_vs_typical": w.peak_quantity / w.typical_level if w.typical_level else None,
            "confirmed_by": sorted(w.confirmed_by),
            "age_seconds": (now - w.first_seen).total_seconds(),
        }

    recent = [e for e in events.recent(iid, 50) if now - e.at <= timedelta(seconds=window_seconds)]
    kinds = {e.kind for e in recent}
    sides = {(e.kind, e.data.get("side")) for e in recent}
    return {
        "price": price,
        "tick": tick,
        "vwap": snap.get("vwap"),
        "vwap_std": snap.get("vwap_std"),
        "delta_1m": snap.get("delta_1m") or 0.0,
        "delta_5m": snap.get("delta_5m") or 0.0,
        "cvd": snap.get("cvd") or 0.0,
        "buy_5m": snap.get("buy_5m") or 0.0,
        "sell_5m": snap.get("sell_5m") or 0.0,
        "aggressive_ratio_5m": snap.get("aggressive_ratio_5m"),
        "imbalance_top5": snap.get("imbalance_top5"),
        "volume_ratio": snap.get("volume_ratio"),
        "spread_ticks": (book.spread / tick) if book is not None and book.spread is not None else None,
        "bid_wall": wall(BID),
        "ask_wall": wall(ASK),
        "recent_events": sorted(kinds),
        "bid_absorption": ("ABSORPTION", "BID") in sides,
        "ask_absorption": ("ABSORPTION", "ASK") in sides,
        "bid_wall_withdrawn": ("WALL_WITHDRAWN", BID) in sides,
        "ask_wall_withdrawn": ("WALL_WITHDRAWN", ASK) in sides,
        "estimated": True,
    }
