"""Market-wide scanner: surface the instruments where something significant is happening.

For every instrument in the universe (up to a few hundred, e.g. the NIFTY 500), the scanner combines:

- day statistics from the brokers' batch quote endpoints: change from the previous close, the opening
  gap, volume against the instrument's normal volume for this point in the session, and breaks of the
  previous day's high or low;
- the market events recorded in the last half hour (walls, withdrawals, absorption, aggressive flow,
  open-interest and IV shocks), for the instruments whose books or chains are being analysed.

It ranks by the significance of what it found; it does not label stocks good or bad.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from jdquant.intelligence.bars import PriceBar
from jdquant.intelligence.events import EventStore
from jdquant.marketdata.instruments import Instrument
from jdquant.markets.sessions import session_for

log = logging.getLogger(__name__)

# category -> (weight, event kinds that count toward it)
CATEGORIES: dict[str, tuple[float, tuple[str, ...]]] = {
    "Unusual volume": (3.0, ("VOLUME_SPIKE",)),
    "Large order-book wall": (2.0, ("WALL_DETECTED",)),
    "Aggressive buying": (2.5, ("AGGRESSIVE_BUYING", "LARGE_TRADE_CLUSTER")),
    "Aggressive selling": (2.5, ("AGGRESSIVE_SELLING",)),
    "Breakout": (3.0, ("BREAKOUT", "STRUCTURE_BREAK")),
    "Breakdown": (3.0, ("BREAKDOWN",)),
    "OI expansion": (2.0, ("OI_SHOCK", "OI_BUILDUP")),
    "IV expansion": (2.0, ("IV_SHOCK",)),
    "Liquidity withdrawal": (3.0, ("WALL_WITHDRAWN", "LIQUIDITY_PULLED")),
    "Absorption": (2.5, ("ABSORPTION",)),
    "Big move": (2.0, ()),
    "Gap": (1.0, ()),
    "News": (1.5, ("NEWS", "CORPORATE_EVENT")),
}


@dataclass
class ScanConfig:
    unusual_volume: float = 2.0  # today's volume vs normal for this time of day
    big_move_pct: float = 2.0
    gap_pct: float = 1.0
    event_minutes: int = 30


class Scanner:
    def __init__(
        self,
        instrument: Callable[[str], Instrument | None],
        snapshots: Callable[[list[Instrument]], dict[str, dict[str, Any]]],
        daily: Callable[[str], list[PriceBar] | None],
        events: EventStore,
        now: Callable[[], datetime],
        config: ScanConfig | None = None,
    ):
        self._instrument = instrument
        self._snapshots = snapshots
        self._daily = daily
        self._events = events
        self._now = now
        self.config = config or ScanConfig()
        self.last: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def run(self, universe: list[str]) -> dict[str, Any]:
        now = self._now()
        instruments = [i for iid in universe if (i := self._instrument(iid)) is not None]
        try:
            stats = self._snapshots(instruments)
        except Exception as exc:  # keep scanning on events alone
            log.warning("scanner quotes failed: %s", exc)
            stats = {}
        counts = self._events.counts_since(now - timedelta(minutes=self.config.event_minutes))
        rows = []
        for instrument in instruments:
            iid = instrument.instrument_id
            row = self._score(instrument, stats.get(iid), counts.get(iid, {}), now)
            if row["tags"]:
                rows.append(row)
        rows.sort(key=lambda r: r["score"], reverse=True)
        result = {
            "at": now.isoformat(),
            "universe": len(universe),
            "priced": len(stats),
            "results": rows,
            "categories": {c: sum(1 for r in rows if c in r["tags"]) for c in CATEGORIES},
        }
        with self._lock:
            self.last = result
        return result

    def _score(self, instrument: Instrument, stats: dict | None, counts: dict[str, int], now: datetime):
        c = self.config
        tags: dict[str, str] = {}
        score = 0.0
        facts: dict[str, Any] = {"instrument_id": instrument.instrument_id, "symbol": instrument.base_asset}
        if stats:
            last, prev = stats.get("last"), stats.get("prev_close")
            facts.update({k: stats.get(k) for k in ("last", "open", "high", "low", "prev_close", "volume")})
            if last and prev:
                change = (last - prev) / prev * 100
                facts["change_pct"] = round(change, 2)
                if abs(change) >= c.big_move_pct:
                    tags["Big move"] = f"{change:+.1f}% on the day"
            if stats.get("open") and prev:
                gap = (stats["open"] - prev) / prev * 100
                facts["gap_pct"] = round(gap, 2)
                if abs(gap) >= c.gap_pct:
                    tags["Gap"] = f"Opened {gap:+.1f}% from the previous close"
            daily = self._daily(instrument.instrument_id) or []
            completed = [b for b in daily if b.start.date() < now.date()]
            if completed and stats.get("volume"):
                avg = sum(b.volume for b in completed[-20:]) / len(completed[-20:])
                fraction = self._session_fraction(instrument, now)
                if avg > 0 and fraction > 0.05:
                    ratio = stats["volume"] / (avg * fraction)
                    facts["volume_ratio"] = round(ratio, 2)
                    if ratio >= c.unusual_volume:
                        tags["Unusual volume"] = f"{ratio:.1f}x normal volume for this time of day"
            if completed and last:
                prev_day = completed[-1]
                twenty_high = max(b.high for b in completed[-20:])
                twenty_low = min(b.low for b in completed[-20:])
                if last > prev_day.high:
                    tags["Breakout"] = f"Above the previous day's high {prev_day.high:g}" + (
                        " and the 20-day high" if last > twenty_high else ""
                    )
                elif last < prev_day.low:
                    tags["Breakdown"] = f"Below the previous day's low {prev_day.low:g}" + (
                        " and the 20-day low" if last < twenty_low else ""
                    )
        for category, (_weight, kinds) in CATEGORIES.items():
            n = sum(counts.get(k, 0) for k in kinds)
            if n and category not in tags:
                tags[category] = f"{n} event{'s' if n > 1 else ''} in {c.event_minutes} min"
        for category in tags:
            score += CATEGORIES[category][0]
        if "change_pct" in facts:
            score += min(3.0, abs(facts["change_pct"]) / 2)
        if "volume_ratio" in facts:
            score += min(3.0, max(0.0, facts["volume_ratio"] - 1))
        return {**facts, "tags": tags, "score": round(score, 2), "events": counts}

    @staticmethod
    def _session_fraction(instrument: Instrument, now: datetime) -> float:
        session = session_for(instrument)
        if session.always_open:
            local = now.astimezone(session.tz)
            return (local.hour * 3600 + local.minute * 60) / 86400 or 1.0
        local = now.astimezone(session.tz)
        opened = datetime.combine(local.date(), session.open_time, session.tz)
        elapsed = (local - opened).total_seconds()
        if elapsed <= 0:
            return 1.0  # before the open: today's volume is yesterday's full session
        return min(1.0, elapsed / session.seconds_per_day)
