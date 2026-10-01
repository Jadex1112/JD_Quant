"""Market events: what the engines observed, stored, searchable and linked into an event graph.

Every detector (order book, order flow, price structure, options, news, data quality) reports what it
sees as a `MarketEvent` with the numbers behind it. The event engine:

- drops repeats of the same observation within a cool-down;
- stores events in the market database, where they can be searched by instrument, kind, time and text;
- links each event to the recent events on the same instrument (or its underlying) that plausibly led
  to it, so a timeline reads "volume spike -> aggressive buying -> ask liquidity pulled -> breakout"
  instead of isolated alerts. A link records sequence, not proof of cause;
- streams new events to listeners (the web UI, strategies, the AI interpreter).

Events describe observations. None of them identifies a participant: an order-book withdrawal is a
fact about displayed liquidity, not evidence of manipulation.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from jdquant.core.clock import Clock
from jdquant.marketdata.recorder import MarketStore

log = logging.getLogger(__name__)

INFO, NOTICE, WARNING, CRITICAL = "INFO", "NOTICE", "WARNING", "CRITICAL"
SEVERITY_RANK = {INFO: 0, NOTICE: 1, WARNING: 2, CRITICAL: 3}

DISCLAIMER = "This does not establish manipulation: participant identity is not available in market data."

# kind -> (category, default severity, cool-down seconds, what it means)
KINDS: dict[str, tuple[str, str, int, str]] = {
    # order book
    "WALL_DETECTED": ("liquidity", NOTICE, 60, "Abnormally large displayed quantity at one price"),
    "WALL_APPROACHED": ("liquidity", INFO, 60, "Price is trading close to a large displayed level"),
    "WALL_REDUCED": ("liquidity", INFO, 30, "A large level lost much of its displayed quantity"),
    "WALL_CONSUMED": ("liquidity", NOTICE, 30, "A large level was traded away"),
    "WALL_WITHDRAWN": ("liquidity", WARNING, 30, "Displayed liquidity disappeared without matching trades"),
    "WALL_PARTIAL": ("liquidity", NOTICE, 30, "A large level was partly traded and partly cancelled"),
    "WALL_MIGRATED": ("liquidity", NOTICE, 30, "Large liquidity reappeared at a nearby price"),
    "WALL_REAPPEARED": ("liquidity", NOTICE, 30, "Large liquidity returned at the same price"),
    "WALL_BROKEN": ("liquidity", NOTICE, 30, "Price traded through a level that held a large wall"),
    "LIQUIDITY_ADDED": ("liquidity", INFO, 60, "Quantity stacked near the best price without trades"),
    "LIQUIDITY_PULLED": ("liquidity", INFO, 60, "Quantity pulled near the best price without trades"),
    # order flow
    "AGGRESSIVE_BUYING": ("flow", NOTICE, 60, "Buyers lifting offers far more than sellers hit bids"),
    "AGGRESSIVE_SELLING": ("flow", NOTICE, 60, "Sellers hitting bids far more than buyers lift offers"),
    "LARGE_TRADE": ("flow", INFO, 5, "A trade much larger than usual for this instrument"),
    "LARGE_TRADE_CLUSTER": ("flow", NOTICE, 30, "Several large trades on the same side in quick succession"),
    "SWEEP": ("flow", NOTICE, 20, "Aggressive orders cleared several price levels at once"),
    "ABSORPTION": ("flow", NOTICE, 60, "Heavy aggressive volume met by passive orders; price held"),
    "EXHAUSTION": ("flow", NOTICE, 120, "Price made a new extreme on fading aggressive volume"),
    "ICEBERG_LIKE": ("flow", NOTICE, 120, "A level kept refilling: far more traded than was ever shown"),
    "ORDER_FLOW_REVERSAL": ("flow", NOTICE, 120, "Cumulative delta turned against its recent direction"),
    "IMBALANCE": ("flow", INFO, 120, "Visible bids and offers are strongly one-sided"),
    # price, volume and volatility
    "VOLUME_SPIKE": ("price", NOTICE, 120, "Volume far above its recent average"),
    "VOLATILITY_SPIKE": ("price", NOTICE, 120, "Price range far above its recent average"),
    "SPREAD_WIDENING": ("price", INFO, 120, "Bid-ask spread far wider than usual"),
    "SPREAD_NORMALIZED": ("price", INFO, 120, "Bid-ask spread back to normal"),
    "BREAKOUT": ("price", NOTICE, 300, "Price closed above a key level"),
    "BREAKDOWN": ("price", NOTICE, 300, "Price closed below a key level"),
    "FAILED_BREAKOUT": ("price", NOTICE, 300, "Price broke a key level and quickly returned"),
    "VWAP_RECLAIM": ("price", INFO, 300, "Price crossed back above VWAP"),
    "VWAP_REJECTION": ("price", INFO, 300, "Price crossed back below VWAP"),
    "STRUCTURE_BREAK": ("price", NOTICE, 300, "Break of structure: a swing high or low was taken out"),
    "CHANGE_OF_CHARACTER": ("price", NOTICE, 300, "Change of character: the trend's last swing failed"),
    "REGIME_CHANGE": ("price", NOTICE, 600, "The market regime changed"),
    "LVN_APPROACH": ("price", INFO, 300, "Price near a low-volume node, where it tends to move fast"),
    # options and futures
    "OI_SHOCK": ("derivatives", NOTICE, 300, "Open interest changed sharply"),
    "IV_SHOCK": ("derivatives", NOTICE, 300, "Implied volatility changed sharply"),
    "OI_BUILDUP": ("derivatives", INFO, 600, "Price and open interest moved together"),
    "PCR_EXTREME": ("derivatives", INFO, 900, "Put-call ratio at an extreme"),
    # context
    "NEWS": ("news", NOTICE, 0, "News about the instrument"),
    "CORPORATE_EVENT": ("news", NOTICE, 0, "Scheduled corporate action or announcement"),
    # data and brokers
    "DATA_DISCREPANCY": ("data", WARNING, 60, "Brokers disagree about the price"),
    "STALE_FEED": ("data", WARNING, 60, "A broker feed stopped updating"),
    "FEED_RECOVERED": ("data", INFO, 60, "A broker feed is updating again"),
    "CROSS_FEED_CONFIRMED": ("data", INFO, 60, "The same observation seen on independent broker feeds"),
}

# kind -> kinds that, seen shortly before on the same instrument, are linked as leading up to it
PRECEDES: dict[str, set[str]] = {
    "WALL_WITHDRAWN": {"WALL_APPROACHED", "WALL_DETECTED", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING"},
    "WALL_CONSUMED": {"WALL_APPROACHED", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "SWEEP", "LARGE_TRADE"},
    "WALL_PARTIAL": {"WALL_APPROACHED", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING"},
    "WALL_APPROACHED": {"WALL_DETECTED", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "VOLUME_SPIKE"},
    "WALL_MIGRATED": {"WALL_WITHDRAWN"},
    "WALL_REAPPEARED": {"WALL_WITHDRAWN"},
    "WALL_BROKEN": {"WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_PARTIAL", "SWEEP", "AGGRESSIVE_BUYING",
                    "AGGRESSIVE_SELLING", "LARGE_TRADE_CLUSTER"},
    "BREAKOUT": {"WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_BROKEN", "SWEEP", "AGGRESSIVE_BUYING",
                 "VOLUME_SPIKE", "LARGE_TRADE_CLUSTER", "NEWS", "LIQUIDITY_PULLED", "OI_BUILDUP"},
    "BREAKDOWN": {"WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_BROKEN", "SWEEP", "AGGRESSIVE_SELLING",
                  "VOLUME_SPIKE", "LARGE_TRADE_CLUSTER", "NEWS", "LIQUIDITY_PULLED", "OI_BUILDUP"},
    "FAILED_BREAKOUT": {"BREAKOUT", "BREAKDOWN", "ABSORPTION", "EXHAUSTION"},
    "AGGRESSIVE_BUYING": {"VOLUME_SPIKE", "NEWS", "CORPORATE_EVENT", "LARGE_TRADE_CLUSTER"},
    "AGGRESSIVE_SELLING": {"VOLUME_SPIKE", "NEWS", "CORPORATE_EVENT", "LARGE_TRADE_CLUSTER"},
    "LARGE_TRADE_CLUSTER": {"NEWS", "VOLUME_SPIKE", "LARGE_TRADE"},
    "SWEEP": {"NEWS", "VOLUME_SPIKE", "LIQUIDITY_PULLED", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING"},
    "VOLUME_SPIKE": {"NEWS", "CORPORATE_EVENT"},
    "VOLATILITY_SPIKE": {"NEWS", "CORPORATE_EVENT", "VOLUME_SPIKE", "SWEEP"},
    "ABSORPTION": {"AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "WALL_APPROACHED"},
    "EXHAUSTION": {"AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "BREAKOUT", "BREAKDOWN"},
    "ORDER_FLOW_REVERSAL": {"ABSORPTION", "EXHAUSTION", "WALL_DETECTED"},
    "STRUCTURE_BREAK": {"SWEEP", "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "WALL_WITHDRAWN", "WALL_BROKEN"},
    "CHANGE_OF_CHARACTER": {"ABSORPTION", "EXHAUSTION", "ORDER_FLOW_REVERSAL", "STRUCTURE_BREAK"},
    "VWAP_RECLAIM": {"AGGRESSIVE_BUYING", "ABSORPTION", "VOLUME_SPIKE"},
    "VWAP_REJECTION": {"AGGRESSIVE_SELLING", "ABSORPTION", "VOLUME_SPIKE"},
    "OI_SHOCK": {"NEWS", "VOLUME_SPIKE", "BREAKOUT", "BREAKDOWN"},
    "IV_SHOCK": {"NEWS", "CORPORATE_EVENT", "VOLATILITY_SPIKE"},
    "OI_BUILDUP": {"BREAKOUT", "BREAKDOWN", "VOLUME_SPIKE"},
    "REGIME_CHANGE": {"BREAKOUT", "BREAKDOWN", "VOLATILITY_SPIKE", "STRUCTURE_BREAK"},
    "SPREAD_WIDENING": {"NEWS", "VOLATILITY_SPIKE", "LIQUIDITY_PULLED"},
    "CROSS_FEED_CONFIRMED": {"WALL_DETECTED", "WALL_WITHDRAWN"},
}  # fmt: skip

LINK_WINDOW = timedelta(minutes=2)
NEWS_LINK_WINDOW = timedelta(minutes=30)


@dataclass
class MarketEvent:
    instrument_id: str
    kind: str
    at: datetime
    title: str
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = ""
    source: str = ""  # the feed or broker it was observed on ("" when derived from several)
    key: str = ""  # identity for de-duplication, e.g. the price of a wall
    event_id: str = ""
    parents: list[str] = field(default_factory=list)
    explanation: str | None = None

    def __post_init__(self) -> None:
        category, severity, _, _ = KINDS.get(self.kind, ("other", INFO, 60, ""))
        self.severity = self.severity or severity
        self.event_id = self.event_id or f"EV-{uuid.uuid4().hex[:12]}"
        self.data.setdefault("category", category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "instrument_id": self.instrument_id,
            "kind": self.kind,
            "category": self.data.get("category", "other"),
            "at": self.at.isoformat(),
            "severity": self.severity,
            "title": self.title,
            "source": self.source,
            "data": {k: v for k, v in self.data.items() if k != "category"},
            "parents": self.parents,
            "explanation": self.explanation,
            "meaning": KINDS.get(self.kind, ("", "", 0, ""))[3],
        }


class EventStore:
    def __init__(self, store: MarketStore):
        self.store = store

    def add(self, event: MarketEvent) -> None:
        body = {**event.data, "_source": event.source, "_explanation": event.explanation}
        self.store.execute(
            "INSERT OR REPLACE INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.instrument_id,
                event.kind,
                event.at.timestamp(),
                event.severity,
                event.title,
                json.dumps(body, default=str, separators=(",", ":")),
            ),
        )
        for parent in event.parents:
            self.store.execute(
                "INSERT OR IGNORE INTO event_links VALUES (?, ?, ?)", (parent, event.event_id, "preceded")
            )

    def set_explanation(self, event_id: str, text: str) -> None:
        rows = self.store.query("SELECT data FROM market_events WHERE event_id = ?", (event_id,))
        if rows:
            body = json.loads(rows[0]["data"])
            body["_explanation"] = text
            self.store.execute(
                "UPDATE market_events SET data = ? WHERE event_id = ?",
                (json.dumps(body, default=str, separators=(",", ":")), event_id),
            )

    @staticmethod
    def _row(row, parents: dict[str, list[str]] | None = None) -> MarketEvent:
        body = json.loads(row["data"])
        source = body.pop("_source", "")
        explanation = body.pop("_explanation", None)
        return MarketEvent(
            row["instrument"],
            row["kind"],
            datetime.fromtimestamp(row["ts"], UTC),
            row["title"],
            body,
            severity=row["severity"],
            source=source,
            event_id=row["event_id"],
            parents=(parents or {}).get(row["event_id"], []),
            explanation=explanation,
        )

    def search(
        self,
        *,
        instrument_id: str | None = None,
        kinds: list[str] | None = None,
        category: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        text: str | None = None,
        min_severity: str | None = None,
        limit: int = 200,
    ) -> list[MarketEvent]:
        where, params = [], []
        if instrument_id:
            where.append("instrument = ?")
            params.append(instrument_id)
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if category:
            cat_kinds = [k for k, v in KINDS.items() if v[0] == category]
            where.append(f"kind IN ({','.join('?' * len(cat_kinds))})")
            params.extend(cat_kinds)
        if since:
            where.append("ts >= ?")
            params.append(since.timestamp())
        if until:
            where.append("ts <= ?")
            params.append(until.timestamp())
        if text:
            where.append("(title LIKE ? OR data LIKE ? OR kind LIKE ?)")
            params.extend([f"%{text}%"] * 3)
        if min_severity:
            allowed = [s for s, r in SEVERITY_RANK.items() if r >= SEVERITY_RANK.get(min_severity, 0)]
            where.append(f"severity IN ({','.join('?' * len(allowed))})")
            params.extend(allowed)
        sql = "SELECT * FROM market_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.store.query(sql + " ORDER BY ts DESC LIMIT ?", (*params, limit))
        return [self._row(r, self._parents([r["event_id"] for r in rows])) for r in rows]

    def _parents(self, ids: list[str]) -> dict[str, list[str]]:
        if not ids:
            return {}
        rows = self.store.query(
            f"SELECT parent, child FROM event_links WHERE child IN ({','.join('?' * len(ids))})", tuple(ids)
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["child"], []).append(r["parent"])
        return out

    def counts_since(self, since: datetime) -> dict[str, dict[str, int]]:
        """Event counts per instrument and kind since a time (for the scanner)."""
        rows = self.store.query(
            "SELECT instrument, kind, COUNT(*) AS n FROM market_events WHERE ts >= ? "
            "GROUP BY instrument, kind",
            (since.timestamp(),),
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["instrument"], {})[r["kind"]] = r["n"]
        return out

    def get(self, event_id: str) -> MarketEvent | None:
        rows = self.store.query("SELECT * FROM market_events WHERE event_id = ?", (event_id,))
        return self._row(rows[0], self._parents([event_id])) if rows else None

    def graph(self, event_id: str, depth: int = 4) -> dict[str, Any]:
        """The event with everything linked before and after it, as nodes and edges."""
        seen: dict[str, MarketEvent] = {}
        edges: set[tuple[str, str]] = set()
        frontier = [event_id]
        for _ in range(depth + 1):
            if not frontier:
                break
            marks = ",".join("?" * len(frontier))
            rows = self.store.query(
                f"SELECT parent, child FROM event_links WHERE parent IN ({marks}) OR child IN ({marks})",
                (*frontier, *frontier),
            )
            nxt = []
            for eid in frontier:
                if eid not in seen and (ev := self.get(eid)) is not None:
                    seen[eid] = ev
            for r in rows:
                edges.add((r["parent"], r["child"]))
                for eid in (r["parent"], r["child"]):
                    if eid not in seen and eid not in nxt:
                        nxt.append(eid)
            frontier = nxt
        for eid in frontier:
            if eid not in seen and (ev := self.get(eid)) is not None:
                seen[eid] = ev
        nodes = sorted(seen.values(), key=lambda e: e.at)
        return {
            "root": event_id,
            "nodes": [e.to_dict() for e in nodes],
            "edges": [{"from": a, "to": b} for a, b in sorted(edges) if a in seen and b in seen],
        }


class EventEngine:
    """De-duplicates, links, stores and fans out market events."""

    def __init__(self, clock: Clock, store: EventStore, *, underlying_of: Callable[[str], str | None] = None):
        self._clock = clock
        self.store = store
        self.underlying_of = underlying_of or (lambda instrument_id: None)
        self._recent: dict[str, deque[MarketEvent]] = {}
        self._last_key: dict[tuple[str, str, str, str], datetime] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[MarketEvent], None]] = []
        self._subscribers: list[queue.Queue] = []
        self.counts: dict[str, int] = {}

    def emit(self, event: MarketEvent) -> MarketEvent | None:
        """Record an observation; returns None when it repeats one inside its cool-down."""
        cooldown = timedelta(seconds=KINDS.get(event.kind, ("", "", 60, ""))[2])
        dedupe = (event.instrument_id, event.kind, event.source, event.key)
        with self._lock:
            last = self._last_key.get(dedupe)
            if cooldown and last is not None and event.at - last < cooldown:
                return None
            self._last_key[dedupe] = event.at
            event.parents = event.parents or self._link(event)
            bucket = self._recent.setdefault(event.instrument_id, deque(maxlen=200))
            bucket.append(event)
            self.counts[event.kind] = self.counts.get(event.kind, 0) + 1
            subscribers = list(self._subscribers)
        try:
            self.store.add(event)
        except Exception:
            log.exception("storing event %s failed", event.event_id)
        payload = event.to_dict()
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:
                log.exception("event listener failed")
        return event

    def _link(self, event: MarketEvent) -> list[str]:
        wanted = PRECEDES.get(event.kind)
        if not wanted:
            return []
        candidates = list(self._recent.get(event.instrument_id, ()))
        underlying = self.underlying_of(event.instrument_id)
        if underlying and underlying != event.instrument_id:
            candidates += list(self._recent.get(underlying, ()))
        parents: dict[str, MarketEvent] = {}
        for prior in candidates:
            window = NEWS_LINK_WINDOW if prior.kind in ("NEWS", "CORPORATE_EVENT") else LINK_WINDOW
            if prior.kind in wanted and timedelta(0) <= event.at - prior.at <= window:
                # keep the most recent event of each kind so the graph stays readable
                if prior.kind not in parents or prior.at > parents[prior.kind].at:
                    parents[prior.kind] = prior
        return [p.event_id for p in sorted(parents.values(), key=lambda e: e.at)]

    def recent(self, instrument_id: str | None = None, limit: int = 50) -> list[MarketEvent]:
        with self._lock:
            if instrument_id is not None:
                items = list(self._recent.get(instrument_id, ()))
            else:
                items = [e for bucket in self._recent.values() for e in bucket]
        return sorted(items, key=lambda e: e.at, reverse=True)[:limit]

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
