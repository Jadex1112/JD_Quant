"""The market intelligence service: wires data sources, engines, events and the API together.

Data flows one way:

    broker feeds / REST depth polls / simulated books
        -> MarketDataHub (normalized, per source)
        -> order book, order flow, data quality, recorder
        -> events (stored, linked, explained)
        -> API, strategies, dashboard

The trading engine receives prices from the hub's primary source only; nothing here places orders.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any

from jdquant.core.errors import NotFoundError, ValidationError
from jdquant.intelligence.bars import (
    PriceBar,
    atr,
    from_candles,
    from_dicts,
    from_flow,
    merge,
    resample,
    split_sessions,
)
from jdquant.intelligence.common import money, session_day, tick_size
from jdquant.intelligence.events import (
    DISCLAIMER,
    KINDS,
    SEVERITY_RANK,
    WARNING,
    EventEngine,
    EventStore,
    MarketEvent,
)
from jdquant.intelligence.futures import FuturesObservation, basis, classify
from jdquant.intelligence.options import OptionChain, analyze, shocks, simulated_chain
from jdquant.intelligence.orderbook import OrderBookEngine, expected_fill
from jdquant.intelligence.orderflow import OrderFlowEngine
from jdquant.intelligence.price import (
    alignment,
    key_levels,
    market_structure,
    multi_timeframe,
    regime,
    volume_profile,
    vwap_state,
)
from jdquant.intelligence.quality import DataQuality
from jdquant.intelligence.scanner import Scanner
from jdquant.marketdata.book import BookSnapshot
from jdquant.marketdata.hub import MarketDataHub
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.recorder import MarketRecorder, MarketStore
from jdquant.markets.sessions import session_for

log = logging.getLogger(__name__)

KIND = "intelligence"
MAX_WATCH = 50
HISTORY_REFRESH = timedelta(minutes=5)
DEMO_SPOTS = {
    "NIFTY": 26_000.0,
    "BANKNIFTY": 59_000.0,
    "FINNIFTY": 27_500.0,
    "MIDCPNIFTY": 13_500.0,
    "SENSEX": 85_000.0,
}
INDEX_SPOT = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}
DATA_USE_NOTICE = (
    "Market data is licensed by the exchanges through your brokers. Recorded data stays on this machine for "
    "your own analysis; do not redistribute it, and check your brokers' API terms."
)


def book_dict(book: BookSnapshot | None, levels: int = 20) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "source": book.source,
        "exchange_time": book.exchange_ts.isoformat(),
        "received": book.received_ts.isoformat(),
        "bids": [
            {"price": lv.price, "quantity": lv.quantity, "orders": lv.orders} for lv in book.bids[:levels]
        ],
        "asks": [
            {"price": lv.price, "quantity": lv.quantity, "orders": lv.orders} for lv in book.asks[:levels]
        ],
        "last_price": book.last_price,
        "last_quantity": book.last_quantity,
        "volume": book.volume,
        "open_interest": book.open_interest,
        "total_buy_quantity": book.total_buy_quantity,
        "total_sell_quantity": book.total_sell_quantity,
        "spread": book.spread,
        "capacity": book.capacity,
        "simulated": bool(book.meta.get("simulated")),
    }


class MarketIntelligence:
    def __init__(
        self,
        platform,
        store,
        market_store: MarketStore,
        *,
        connections=None,
        live=None,
        chat=None,
        forward_quote=None,
        simulated_depth: bool = False,
    ):
        self._p = platform
        self._store = store
        self._connections = connections
        self._live = live
        self._chat = chat
        clock = platform.clock
        self.clock = clock
        self.hub = MarketDataHub(clock, forward_quote=forward_quote, primary_for=self._primary)
        self.recorder = MarketRecorder(market_store, clock)
        self.event_store = EventStore(market_store)
        self.events = EventEngine(clock, self.event_store, underlying_of=self._underlying_of)
        self.orderbook = OrderBookEngine(self.events.emit, self.instrument, other_books=self.hub.books)
        self.flow = OrderFlowEngine(self.events.emit, self.instrument)
        self.quality = DataQuality(self.events.emit, self.instrument, clock.now)
        self.scanner = Scanner(
            self.instrument, self._snapshots, self.daily_cached, self.event_store, clock.now
        )
        for listener in (
            self.orderbook.on_book,
            self.flow.on_book,
            self.quality.on_book,
            self.recorder.on_book,
        ):
            self.hub.book_listeners.append(listener)
        for listener in (self.flow.on_tick, self.recorder.on_tick):
            self.hub.tick_listeners.append(listener)
        self.watch: list[str] = []
        self.scanner_universe: list[str] = []
        self.scanner_enabled = False
        self.option_underlyings: list[str] = []
        self.explain_events = False
        self.explain_min_severity = WARNING
        self.streaming_enabled = True
        self.deep_depth: list[str] = []
        self.feeds = None  # connectivity.feeds.manager.FeedManager, attached by the app
        self.periodic: list[tuple[str, float, Any]] = []  # (name, seconds, fn): extra periodic work
        self._analysis: dict[str, dict[str, Any]] = {}
        self._history: dict[str, tuple[datetime, list[PriceBar]]] = {}
        self._daily: dict[str, tuple[date, list[PriceBar]]] = {}
        self._chains: dict[str, tuple[OptionChain, OptionChain | None, dict[str, Any]]] = {}
        self._futures_ref: dict[str, tuple[date, float, float]] = {}
        self._futures_state: dict[str, str | None] = {}
        self._stale_feeds: set[str] = set()
        self._breakouts: dict[str, deque] = {}
        self.rest_latency: dict[str, float] = {}
        self.rejections: deque = deque(maxlen=500)  # (time, venue, reason)
        self._explain_queue: deque = deque(maxlen=50)
        self._explained_today: tuple[date, int] = (clock.now().date(), 0)
        self._last: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.simulated = None
        if simulated_depth:
            from jdquant.marketdata.simdepth import SimulatedDepthFeed

            self.simulated = SimulatedDepthFeed(
                self.hub.publish, self._simulated_instruments, self._start_price, clock.now
            )
        from jdquant.intelligence.news import NewsDesk
        from jdquant.intelligence.replay import ReplayManager

        self.news = NewsDesk(
            store,
            self.events,
            self.event_store,
            instruments=platform.instruments.all,
            bars=lambda iid: self.minute_bars(iid)[0],
            now=clock.now,
        )
        self.replay = ReplayManager(self.recorder, self.event_store, self.instrument)
        self.events.listeners.append(self._on_event)
        platform.bus.subscribe("order.state.changed", self._on_order_state)
        self._load()

    # ---- settings ---------------------------------------------------------------------------------------

    def _load(self) -> None:
        doc = self._store.get(KIND, "settings") or {}
        self.watch = [i for i in doc.get("watch", []) if self.instrument(i) is not None]
        self.scanner_universe = doc.get("scanner_universe", [])
        self.scanner_enabled = bool(doc.get("scanner_enabled", False))
        self.option_underlyings = doc.get("option_underlyings", [])
        self.explain_events = bool(doc.get("explain_events", False))
        self.explain_min_severity = doc.get("explain_min_severity", WARNING)
        self.streaming_enabled = bool(doc.get("streaming_enabled", True))
        self.deep_depth = [i for i in doc.get("deep_depth", []) if i in self.watch][:5]
        rec = doc.get("recording") or {}
        self.recorder.acknowledged_by = rec.get("acknowledged_by")
        self.recorder.retention_days = int(rec.get("retention_days", 30))
        self.recorder.enabled = bool(rec.get("enabled")) and bool(self.recorder.acknowledged_by)

    def _save(self) -> None:
        self._store.put(KIND, "settings", self.settings())

    def settings(self) -> dict[str, Any]:
        return {
            "watch": self.watch,
            "scanner_universe": self.scanner_universe,
            "scanner_enabled": self.scanner_enabled,
            "option_underlyings": self.option_underlyings,
            "explain_events": self.explain_events,
            "explain_min_severity": self.explain_min_severity,
            "streaming_enabled": self.streaming_enabled,
            "deep_depth": self.deep_depth,
            "recording": {
                "enabled": self.recorder.enabled,
                "acknowledged_by": self.recorder.acknowledged_by,
                "retention_days": self.recorder.retention_days,
            },
            "data_use_notice": DATA_USE_NOTICE,
            "max_watch": MAX_WATCH,
        }

    def configure(self, changes: dict[str, Any], user_id: str) -> dict[str, Any]:
        problems = []
        if "watch" in changes:
            watch = list(dict.fromkeys(changes["watch"] or []))
            unknown = [i for i in watch if self.instrument(i) is None]
            if unknown:
                problems.append(
                    {"field": "watch", "message": f"unknown instruments: {', '.join(unknown[:5])}"}
                )
            if len(watch) > MAX_WATCH:
                problems.append({"field": "watch", "message": f"at most {MAX_WATCH} instruments"})
        if "scanner_universe" in changes:
            universe = list(dict.fromkeys(changes["scanner_universe"] or []))
            if len(universe) > 1000:
                problems.append({"field": "scanner_universe", "message": "at most 1000 instruments"})
        if "explain_min_severity" in changes and changes["explain_min_severity"] not in SEVERITY_RANK:
            problems.append(
                {"field": "explain_min_severity", "message": f"one of {', '.join(SEVERITY_RANK)}"}
            )
        rec = changes.get("recording") or {}
        if rec.get("enabled") and not (rec.get("acknowledge") or self.recorder.acknowledged_by):
            problems.append({"field": "recording", "message": "acknowledge the data-use notice first"})
        if problems:
            raise ValidationError("INVALID_SETTINGS", problems)
        with self._lock:
            if "watch" in changes:
                self.watch = watch
            if "scanner_universe" in changes:
                self.scanner_universe = [i for i in universe if self.instrument(i) is not None]
            if "scanner_enabled" in changes:
                self.scanner_enabled = bool(changes["scanner_enabled"])
            if "option_underlyings" in changes:
                self.option_underlyings = [u.upper() for u in changes["option_underlyings"] or []][:10]
            if "explain_events" in changes:
                self.explain_events = bool(changes["explain_events"])
            if "explain_min_severity" in changes:
                self.explain_min_severity = changes["explain_min_severity"]
            if "streaming_enabled" in changes:
                self.streaming_enabled = bool(changes["streaming_enabled"])
            if "deep_depth" in changes:
                self.deep_depth = [i for i in changes["deep_depth"] or [] if i in self.watch][:5]
            self.deep_depth = [i for i in self.deep_depth if i in self.watch]
            if rec:
                self.recorder.configure(
                    enabled=rec.get("enabled"),
                    acknowledged_by=user_id if rec.get("acknowledge") else None,
                    retention_days=rec.get("retention_days"),
                )
            self._save()
        return self.settings()

    # ---- lookups ----------------------------------------------------------------------------------------

    def instrument(self, instrument_id: str) -> Instrument | None:
        try:
            return self._p.instruments.get(instrument_id)
        except NotFoundError:
            return None

    def _primary(self, instrument_id: str) -> str | None:
        if self._connections is not None:
            adapter = self._connections.data_source_for(instrument_id)
            if adapter is not None:
                return adapter.venue
        return None

    def _underlying_of(self, instrument_id: str) -> str | None:
        if instrument_id.startswith("OPT:"):
            return INDEX_SPOT.get(instrument_id[4:], f"NSE:{instrument_id[4:]}-EQ")
        instrument = self.instrument(instrument_id)
        if instrument is not None and instrument.underlying:
            return f"{instrument.venue}:{instrument.underlying}"
        return None

    def features(self, instrument_id: str) -> dict[str, Any]:
        """Live features for strategies (order flow, walls, VWAP, recent events)."""
        from jdquant.intelligence import features

        instrument = self.instrument(instrument_id)
        if instrument is None:
            return {}
        return features.build(
            instrument,
            flow=self.flow,
            orderbook=self.orderbook,
            events=self.events,
            book=self.hub.book(instrument_id),
            now=self.clock.now(),
        )

    def trade_context(self, instrument_id: str, at: datetime) -> dict[str, Any]:
        """What the engines saw when a trade was opened, for the journal."""
        analysis = self._analysis.get(instrument_id) or {}
        flow = self.flow.snapshot(instrument_id) or {}
        vw = (analysis.get("vwap") or {}).get("vwap") or flow.get("vwap")
        price = flow.get("last_price") or analysis.get("last")
        before = [
            e.kind
            for e in self.events.recent(instrument_id, 50)
            if timedelta(0) <= at - e.at <= timedelta(minutes=10)
        ]
        return {
            "regime": (analysis.get("regime") or {}).get("state"),
            "trend": (analysis.get("regime") or {}).get("trend"),
            "events_before": sorted(set(before)),
            "delta_5m": flow.get("delta_5m"),
            "imbalance": flow.get("imbalance_top5"),
            "vs_vwap": None if not (vw and price) else ("ABOVE" if price > vw else "BELOW"),
        }

    def depth_watched(self) -> list[str]:
        return list(self.watch)

    def _simulated_instruments(self) -> list[Instrument]:
        return [
            i for iid in self.watch if (i := self.instrument(iid)) is not None and self._primary(iid) is None
        ]

    def _start_price(self, instrument: Instrument) -> float:
        from jdquant.platform import DEMO_PRICES

        ref = self._p.market.reference_price(instrument.instrument_id) or DEMO_PRICES.get(
            instrument.instrument_id
        )
        return float(ref) if ref else 500.0

    def simulated_covers(self, instrument_id: str) -> bool:
        return self.simulated is not None and self.simulated.covers(instrument_id)

    def streamed(self, instrument_id: str) -> bool:
        """True while a live broker WebSocket (or the demo feed) is delivering this instrument."""
        if self.feeds is not None and self.feeds.covers(instrument_id):
            return True
        return self.simulated_covers(instrument_id)

    # ---- lifecycle --------------------------------------------------------------------------------------

    def start(self) -> None:
        self.hub.start()
        self.recorder.start()
        if self.simulated is not None:
            self.simulated.start()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="market-intelligence", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.feeds is not None:
            self.feeds.stop()
        if self.simulated is not None:
            self.simulated.stop()
        self.hub.stop()
        self.recorder.stop()

    def _loop(self) -> None:
        while not self._stop.wait(5.0):
            try:
                self.tick()
            except Exception:
                log.exception("intelligence cycle failed")

    def _due(self, key: str, seconds: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(key, 0.0) >= seconds:
            self._last[key] = now
            return True
        return False

    def tick(self, *, force: bool = False) -> None:
        """One cycle of the periodic work; `force` runs everything now (tests)."""
        if self.feeds is not None:
            self.feeds.enabled = self.streaming_enabled
            self.feeds.deep = list(self.deep_depth)
            try:
                self.feeds.sync(list(self.watch))
            except Exception:
                log.exception("feed sync failed")
        if force or self._due("analysis", 30):
            for iid in list(self.watch):
                try:
                    self.analyze(iid)
                except Exception:
                    log.exception("analysis of %s failed", iid)
            self._futures()
        if force or self._due("feeds", 10):
            self._check_feeds()
        if force or self._due("options", 60):
            for underlying in list(self.option_underlyings):
                try:
                    self.refresh_chain(underlying)
                except Exception as exc:
                    log.warning("option chain for %s failed: %s", underlying, exc)
        if (force or self._due("scanner", 60)) and self.scanner_enabled and self.scanner_universe:
            self.scanner.run(self.scanner_universe)
        if (force or self._due("news", 300)) and self.news.feeds:
            try:
                self.news.poll()
            except Exception:
                log.exception("news poll failed")
        if force or self._due("corporate", 60):
            self.news.raise_due()
        for name, seconds, fn in list(self.periodic):
            if force or self._due(name, seconds):
                try:
                    fn()
                except Exception:
                    log.exception("%s failed", name)
        if force or self._due("daily", 20):
            self._warm_daily(limit=10 if not force else 1000)
        self._explain_pending()

    # ---- bars and history -------------------------------------------------------------------------------

    def _source(self, instrument_id: str):
        return self._connections.data_source_for(instrument_id) if self._connections is not None else None

    def minute_bars(self, instrument_id: str) -> tuple[list[PriceBar], str]:
        """Broker minute history merged with bars built from live trades; the origin is returned too."""
        instrument = self.instrument(instrument_id)
        now = self.clock.now()
        history: list[PriceBar] = []
        cached = self._history.get(instrument_id)
        source = self._source(instrument_id)
        if cached is not None and now - cached[0] < HISTORY_REFRESH:
            history = cached[1]
        elif source is not None and instrument is not None:
            try:
                history = from_candles(source.fetch_candles(instrument, 60, 900))
                self._history[instrument_id] = (now, history)
            except Exception as exc:
                log.warning("minute history for %s unavailable: %s", instrument_id, exc)
                history = cached[1] if cached else []
        flow = from_flow(self.flow.bars(instrument_id))
        if history or flow:
            origin = (
                "broker history + live trades"
                if history and flow
                else "broker history"
                if history
                else "live trades"
            )
            return merge(history, flow), origin
        if self._live is not None:
            rows = self._live.candles(instrument_id, 60, 600)
            return from_dicts(rows), "live quotes (no volume)"
        return [], "none"

    def daily_cached(self, instrument_id: str) -> list[PriceBar] | None:
        cached = self._daily.get(instrument_id)
        return cached[1] if cached and cached[0] == self.clock.now().date() else None

    def daily(self, instrument_id: str) -> list[PriceBar]:
        cached = self.daily_cached(instrument_id)
        if cached is not None:
            return cached
        instrument, source = self.instrument(instrument_id), self._source(instrument_id)
        bars: list[PriceBar] = []
        if instrument is not None and source is not None:
            try:
                bars = from_candles(source.fetch_candles(instrument, 86400, 60))
            except Exception as exc:
                log.info("daily history for %s unavailable: %s", instrument_id, exc)
        self._daily[instrument_id] = (self.clock.now().date(), bars)
        return bars

    def _warm_daily(self, limit: int) -> None:
        """Fetch daily history for a few scanner instruments per cycle, so scans have averages."""
        if not self.scanner_enabled:
            return
        missing = [i for i in self.scanner_universe if self.daily_cached(i) is None][:limit]
        for iid in missing:
            self.daily(iid)

    def _snapshots(self, instruments: list[Instrument]) -> dict[str, dict[str, Any]]:
        """Batch day statistics, grouped by the broker that prices each instrument."""
        groups: dict[int, tuple[Any, list[Instrument]]] = {}
        for instrument in instruments:
            source = self._source(instrument.instrument_id)
            if source is None:
                continue
            groups.setdefault(id(source), (source, []))[1].append(instrument)
        out: dict[str, dict[str, Any]] = {}
        for source, group in groups.values():
            started = time.monotonic()
            try:
                out.update(source.fetch_snapshots(group))
                self.record_rest(source.venue, (time.monotonic() - started) * 1000)
            except Exception as exc:
                log.warning("batch quotes from %s failed: %s", source.venue, exc)
        for instrument in instruments:
            if instrument.instrument_id in out:
                continue
            flow = self.flow.snapshot(instrument.instrument_id)
            if flow and flow.get("last_price"):
                out[instrument.instrument_id] = {"last": flow["last_price"], "volume": flow["day_volume"]}
        return out

    def record_rest(self, source: str, ms: float) -> None:
        prev = self.rest_latency.get(source)
        self.rest_latency[source] = ms if prev is None else 0.8 * prev + 0.2 * ms

    # ---- price analysis ---------------------------------------------------------------------------------

    def analyze(self, instrument_id: str) -> dict[str, Any]:
        instrument = self.instrument(instrument_id)
        if instrument is None:
            raise NotFoundError("INSTRUMENT_NOT_FOUND", f"unknown instrument {instrument_id}")
        bars, origin = self.minute_bars(instrument_id)
        tick = tick_size(instrument)

        def day_of(t):
            return session_day(instrument, t)

        result: dict[str, Any] = {
            "instrument_id": instrument_id,
            "origin": origin,
            "bars": len(bars),
            "at": self.clock.now().isoformat(),
        }
        if len(bars) < 10:
            result["message"] = "Not enough bars yet"
            self._analysis[instrument_id] = result
            return result
        sessions = split_sessions(bars, day_of)
        today = sessions[max(sessions)]
        has_volume = any(b.volume for b in today)
        atr_value = atr(bars)[-1]
        five = resample(bars, 300)
        session = session_for(instrument)
        now = self.clock.now()
        local = now.astimezone(session.tz)
        elapsed = left = None
        if not session.always_open:
            opened = datetime.combine(local.date(), session.open_time, session.tz)
            closes = datetime.combine(local.date(), session.close_time, session.tz)
            elapsed = (now - opened).total_seconds() / 60
            left = (closes - now).total_seconds() / 60
        daily = self.daily_cached(instrument_id)
        frames = multi_timeframe(bars, daily_bars=daily, day_of=day_of)
        result.update(
            {
                "last": bars[-1].close,
                "vwap": vwap_state(today, tick) if has_volume else None,
                "profile": volume_profile(
                    today, at_price=self.flow.volume_at_price(instrument_id) or None, tick=tick
                )
                if has_volume
                else None,
                "levels": key_levels(bars, day_of, atr_value=atr_value),
                "structure": market_structure(five[-150:]),
                "timeframes": frames,
                "alignment": alignment(frames),
                "regime": regime(five, session_minutes_elapsed=elapsed, session_minutes_left=left),
                "atr_1m": atr_value,
            }
        )
        self._price_events(instrument, result, self._analysis.get(instrument_id), bars)
        self._analysis[instrument_id] = result
        return result

    def _price_events(
        self, instrument: Instrument, now: dict, before: dict | None, bars: list[PriceBar]
    ) -> None:
        iid = instrument.instrument_id
        at = bars[-1].start + timedelta(minutes=1)
        last = now["last"]

        def emit(kind, title, data, key=""):
            self.events.emit(MarketEvent(iid, kind, at, title, data, key=key))

        vw = now.get("vwap")
        if vw and vw.get("recent_cross"):
            cross = vw["recent_cross"]
            prev_cross = ((before or {}).get("vwap") or {}).get("recent_cross")
            if cross != prev_cross:
                verb = "reclaimed" if cross["kind"] == "VWAP_RECLAIM" else "lost"
                emit(
                    cross["kind"],
                    f"Price {verb} VWAP {money(instrument, vw['vwap'])}",
                    {"vwap": vw["vwap"], "price": last, "slope": vw["slope"]},
                    key=str(cross["time"]),
                )
        levels = now.get("levels") or {}
        marks = []
        if levels.get("previous_day"):
            marks += [
                ("previous day high", levels["previous_day"]["high"], 1),
                ("previous day low", levels["previous_day"]["low"], -1),
            ]
        if (orr := levels.get("opening_range")) and orr.get("complete"):
            marks += [("opening range high", orr["high"], 1), ("opening range low", orr["low"], -1)]
        prev_close = bars[-2].close if len(bars) > 1 else last
        recent = self._breakouts.setdefault(iid, deque(maxlen=10))
        for name, level, direction in marks:
            if direction > 0 and prev_close <= level < last:
                emit(
                    "BREAKOUT",
                    f"Closed above the {name} {money(instrument, level)}",
                    {"level": level, "level_name": name, "price": last},
                    key=f"{name}:{level}",
                )
                recent.append((at, name, level, 1))
            elif direction < 0 and prev_close >= level > last:
                emit(
                    "BREAKDOWN",
                    f"Closed below the {name} {money(instrument, level)}",
                    {"level": level, "level_name": name, "price": last},
                    key=f"{name}:{level}",
                )
                recent.append((at, name, level, -1))
        for when, name, level, direction in list(recent):
            if at - when > timedelta(minutes=10):
                continue
            back_inside = (direction > 0 and last < level) or (direction < 0 and last > level)
            if back_inside:
                emit(
                    "FAILED_BREAKOUT",
                    f"Back {'below' if direction > 0 else 'above'} the {name} "
                    f"{money(instrument, level)} within {int((at - when).total_seconds() // 60)} min",
                    {"level": level, "level_name": name, "price": last},
                    key=f"{name}:{level}",
                )
                recent.remove((when, name, level, direction))
        event = (now.get("structure") or {}).get("last_event")
        prev_event = ((before or {}).get("structure") or {}).get("last_event")
        if event and event != prev_event and before is not None:
            kind = "STRUCTURE_BREAK" if event["kind"] == "BOS" else "CHANGE_OF_CHARACTER"
            words = "Break of structure" if kind == "STRUCTURE_BREAK" else "Change of character"
            emit(
                kind,
                f"{words} {event['direction'].lower()} through {money(instrument, event['level'])} (5m)",
                {**event, "timeframe": "5m"},
                key=str(event["time"]),
            )
        reg, prev_reg = now.get("regime") or {}, (before or {}).get("regime") or {}
        if (
            before is not None
            and reg.get("state")
            and prev_reg.get("state")
            and reg["state"] != prev_reg["state"]
        ):
            emit(
                "REGIME_CHANGE",
                f"Regime: {prev_reg['state'].replace('_', ' ').lower()} -> "
                f"{reg['state'].replace('_', ' ').lower()} ({reg['confidence']}% confidence)",
                {
                    "from": prev_reg["state"],
                    "to": reg["state"],
                    "confidence": reg["confidence"],
                    "evidence": reg.get("evidence"),
                },
                key=reg["state"],
            )
        profile = now.get("profile") or {}
        tick = tick_size(instrument)
        for lvn in profile.get("lvn", []):
            if abs(last - lvn) <= 2 * max(tick, profile.get("bin_width", tick)):
                emit(
                    "LVN_APPROACH",
                    f"Price {money(instrument, last)} at a low-volume node {money(instrument, lvn)}",
                    {"lvn": lvn, "price": last, "poc": profile.get("poc")},
                    key=f"{lvn:.6g}",
                )
                break

    # ---- futures ----------------------------------------------------------------------------------------

    def _futures(self) -> None:
        for iid in list(self.watch):
            view = self.futures_view(iid)
            if not view or not view.get("positioning"):
                continue
            state = view["positioning"].get("state")
            if state and state != "NEUTRAL" and self._futures_state.get(iid) != state:
                instrument = self.instrument(iid)
                self.events.emit(
                    MarketEvent(
                        iid,
                        "OI_BUILDUP",
                        self.clock.now(),
                        view["positioning"]["reading"],
                        {
                            **view["positioning"],
                            "price": view.get("price"),
                            "open_interest": view.get("open_interest"),
                            "instrument": instrument.symbol if instrument else iid,
                        },
                        key=state,
                    )
                )
            self._futures_state[iid] = state

    def futures_view(self, instrument_id: str) -> dict[str, Any] | None:
        instrument = self.instrument(instrument_id)
        if instrument is None or not instrument.is_future:
            return None
        book = self.hub.book(instrument_id)
        if book is None or book.last_price is None:
            return {"instrument_id": instrument_id, "message": "No live data yet"}
        today = session_day(instrument, book.exchange_ts)
        ref = self._futures_ref.get(instrument_id)
        if ref is None or ref[0] != today:
            if book.open_interest is None:
                return {
                    "instrument_id": instrument_id,
                    "price": book.last_price,
                    "message": "This feed does not report open interest",
                }
            ref = self._futures_ref[instrument_id] = (today, book.last_price, book.open_interest)
        obs = FuturesObservation(
            instrument_id,
            book.last_price,
            book.open_interest,
            book.exchange_ts,
            instrument.expiry,
            ref[1],
            ref[2],
            book.volume,
        )
        out = {
            "instrument_id": instrument_id,
            "price": book.last_price,
            "open_interest": book.open_interest,
            "expiry": instrument.expiry.isoformat() if instrument.expiry else None,
            "positioning": classify(obs),
            "reference": "first price and open interest seen this session",
        }
        spot_id = self._underlying_of(instrument_id)
        spot = self._p.market.reference_price(spot_id) if spot_id else None
        if spot:
            out["basis"] = basis(book.last_price, float(spot), instrument.expiry, self.clock.now())
        return out

    # ---- options ----------------------------------------------------------------------------------------

    def chain_sources(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        if self._connections is not None:
            for adapter in list(self._connections.adapters.values()):
                for u in adapter.option_underlyings():
                    out.setdefault(u, []).append(adapter.venue)
        return out

    def refresh_chain(self, underlying: str, expiry: date | None = None) -> dict[str, Any]:
        underlying = underlying.upper()
        chain: OptionChain | None = None
        errors = []
        if self._connections is not None:
            for adapter in list(self._connections.adapters.values()):
                if underlying not in adapter.option_underlyings() or not adapter.is_ready():
                    continue
                try:
                    chain = adapter.fetch_option_chain(underlying, expiry)
                except Exception as exc:
                    errors.append(f"{adapter.venue}: {exc}")
                    continue
                if chain is not None:
                    break
        if chain is None:
            if self.simulated is None:
                raise NotFoundError(
                    "OPTION_CHAIN_UNAVAILABLE",
                    "no connected broker supplies this option chain"
                    + (f" ({'; '.join(errors)})" if errors else " (Fyers or Dhan provide index chains)"),
                )
            spot_id = INDEX_SPOT.get(underlying)
            ref = self._p.market.reference_price(spot_id) if spot_id else None
            chain = simulated_chain(
                underlying, float(ref) if ref else DEMO_SPOTS.get(underlying, 1000.0), self.clock.now()
            )
        previous = self._chains.get(underlying)
        prior = previous[0] if previous else None
        first = previous[1] if previous and previous[1] is not None else prior
        result = analyze(chain, first)
        if prior is not None and prior.expiry == chain.expiry and not chain.simulated:
            iid = f"OPT:{underlying}"
            for s in shocks(chain, prior):
                title = {
                    "OI_SHOCK": lambda s: (
                        f"{underlying} {s['strike']:g} {s['option']} open interest "
                        f"{s['change_pct']:+.0f}% ({s['change']:+,.0f})"
                    ),
                    "IV_SHOCK": lambda s: (
                        f"{underlying} ATM IV {s['previous'] * 100:.1f}% -> {s['atm_iv'] * 100:.1f}%"
                    ),
                    "PCR_EXTREME": lambda s: f"{underlying} put-call ratio {s['pcr']:.2f}",
                }[s["kind"]](s)
                self.events.emit(
                    MarketEvent(
                        iid,
                        s["kind"],
                        chain.at,
                        title,
                        dict(s),
                        source=chain.source,
                        key=str(s.get("strike", "")),
                    )
                )
        self._chains[underlying] = (chain, first, result)
        return result

    def chain(self, underlying: str) -> dict[str, Any]:
        cached = self._chains.get(underlying.upper())
        if cached is None or self.clock.now() - cached[0].at > timedelta(minutes=2):
            return self.refresh_chain(underlying)
        return cached[2]

    # ---- feeds and health -------------------------------------------------------------------------------

    def _check_feeds(self) -> None:
        now = self.clock.now()
        for s in self.hub.sources():
            name, quiet = s["source"], s["seconds_since_message"]
            if s["kind"] != "stream" or not s["connected"] or quiet is None:
                continue
            instruments = [i for iid in s["instruments"] if (i := self.instrument(iid)) is not None]
            open_now = any(session_for(i).is_open(now) for i in instruments)
            if quiet > 60 and open_now and name not in self._stale_feeds:
                self._stale_feeds.add(name)
                self.events.emit(
                    MarketEvent(
                        f"FEED:{name}",
                        "STALE_FEED",
                        now,
                        f"{name} feed silent for {quiet:.0f}s",
                        {"source": name, "seconds": quiet},
                        source=name,
                    )
                )
            elif quiet <= 10 and name in self._stale_feeds:
                self._stale_feeds.discard(name)
                self.events.emit(
                    MarketEvent(
                        f"FEED:{name}",
                        "FEED_RECOVERED",
                        now,
                        f"{name} feed updating again",
                        {"source": name},
                        source=name,
                    )
                )

    def _on_order_state(self, event) -> None:
        payload = event.payload
        if payload.get("to") != "REJECTED":
            return
        order = payload.get("order")
        reason = payload.get("reason") or ""
        if order is not None and not str(reason).startswith("RISK_"):
            self.rejections.append((self.clock.now(), order.account_id, str(reason)))

    def health(self) -> dict[str, Any]:
        now = self.clock.now()
        connections = []
        if self._connections is not None:
            for c in list(self._connections.connections.values()):
                adapter = self._connections.adapters.get(c.connection_id)
                recent = [
                    r for r in self.rejections if r[1] == c.account_id and now - r[0] < timedelta(hours=1)
                ]
                connections.append(
                    {
                        "connection_id": c.connection_id,
                        "name": c.name,
                        "venue": c.venue,
                        "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                        "ready": bool(adapter and adapter.is_ready()),
                        "last_error": getattr(c, "last_error", None),
                        "rest_latency_ms": None
                        if c.venue not in self.rest_latency
                        else round(self.rest_latency[c.venue], 1),
                        "rejections_last_hour": len(recent),
                        "circuit_open": bool(adapter and adapter.circuit_open_until > time.monotonic()),
                    }
                )
        return {
            "at": now.isoformat(),
            "connections": connections,
            "feeds": self.hub.sources(),
            "streams": self.feeds.status() if self.feeds is not None else [],
            "stale_feeds": sorted(self._stale_feeds),
        }

    # ---- AI explanations --------------------------------------------------------------------------------

    def _on_event(self, event: MarketEvent) -> None:
        if self.explain_events and SEVERITY_RANK.get(event.severity, 0) >= SEVERITY_RANK.get(
            self.explain_min_severity, 2
        ):
            self._explain_queue.append(event.event_id)

    def _explain_pending(self) -> None:
        day, used = self._explained_today
        if day != self.clock.now().date():
            day, used = self.clock.now().date(), 0
        while self._explain_queue and used < 200:
            event_id = self._explain_queue.popleft()
            try:
                self.explain(event_id)
                used += 1
            except Exception:
                log.exception("explaining %s failed", event_id)
            break  # one per cycle keeps the model's load and cost low
        self._explained_today = (day, used)

    def explain(self, event_id: str) -> dict[str, Any]:
        event = self.event_store.get(event_id)
        if event is None:
            raise NotFoundError("EVENT_NOT_FOUND", f"unknown event {event_id}")
        graph = self.event_store.graph(event_id, depth=2)
        before = [n for n in graph["nodes"] if n["event_id"] != event_id and n["at"] <= event.at.isoformat()]
        flow = (
            self.flow.snapshot(event.instrument_id)
            if not event.instrument_id.startswith(("OPT:", "FEED:"))
            else None
        )
        analysis = self._analysis.get(event.instrument_id) or {}
        context = {
            "last_price": (flow or {}).get("last_price") or analysis.get("last"),
            "order_flow": {k: v for k, v in (flow or {}).items() if k not in ("bars", "large_trades")},
            "regime": (analysis.get("regime") or {}).get("state"),
            "vwap": (analysis.get("vwap") or {}).get("vwap"),
            "levels": {k: v for k, v in (analysis.get("levels") or {}).items() if k != "zones"},
        }
        text, watch_next, by = None, None, "template"
        if self._chat is not None:
            from jdquant.ai.prompts import EVENT_INTERPRETER

            answer = self._chat.ask(
                EVENT_INTERPRETER,
                {"event": event.to_dict(), "leading_events": before[-8:], "context": context},
                max_tokens=1200,
                thinking=False,
            )
            parsed = _json_object(answer)
            if parsed and parsed.get("explanation"):
                text, watch_next, by = (
                    str(parsed["explanation"]),
                    parsed.get("watch_next"),
                    self._chat.provider,
                )
        if text is None:
            text = describe(event, before)
        full = text + (f" Watch next: {watch_next}" if watch_next else "")
        if event.data.get("category") == "liquidity" and "manipulation" not in full:
            full += " " + DISCLAIMER
        self.event_store.set_explanation(event_id, full)
        return {"event_id": event_id, "explanation": full, "by": by, "leading_events": before}

    # ---- views for the API ------------------------------------------------------------------------------

    def overview(self, instrument_id: str, levels: int = 20) -> dict[str, Any]:
        instrument = self.instrument(instrument_id)
        if instrument is None:
            raise NotFoundError("INSTRUMENT_NOT_FOUND", f"unknown instrument {instrument_id}")
        books = self.hub.books(instrument_id)
        analysis = self._analysis.get(instrument_id)
        if analysis is None:
            try:
                analysis = self.analyze(instrument_id)
            except Exception as exc:
                analysis = {"message": f"analysis unavailable: {exc}"}
        flow = self.flow.snapshot(instrument_id)
        size = float(instrument.lot_size or 1) * 100
        primary = books.get(self._primary(instrument_id) or "") or self.hub.book(instrument_id)
        return {
            "instrument_id": instrument_id,
            "symbol": instrument.symbol,
            "quote_asset": instrument.quote_asset,
            "tick_size": float(instrument.tick_size),
            "watched": instrument_id in self.watch,
            "simulated": self.simulated_covers(instrument_id),
            "books": {s: book_dict(b, levels) for s, b in books.items()},
            "primary_source": primary.source if primary else None,
            "walls": self.orderbook.walls(instrument_id),
            "flow": flow,
            "analysis": analysis,
            "quality": (self.quality.report(instrument_id) or [None])[0],
            "futures": self.futures_view(instrument_id),
            "execution_estimate": {
                "quantity": size,
                "buy": expected_fill(primary, "BUY", size) if primary else None,
                "sell": expected_fill(primary, "SELL", size) if primary else None,
            },
            "events": [e.to_dict() for e in self.events.recent(instrument_id, 60)],
            "disclaimer": DISCLAIMER,
        }

    def dashboard(self) -> dict[str, Any]:
        now = self.clock.now()
        instruments = []
        for iid in self.watch[:12]:
            instrument = self.instrument(iid)
            if instrument is None:
                continue
            analysis = self._analysis.get(iid) or {}
            flow = self.flow.snapshot(iid) or {}
            price = (
                flow.get("last_price")
                or analysis.get("last")
                or (float(p) if (p := self._p.market.reference_price(iid)) is not None else None)
            )
            daily = self.daily_cached(iid) or []
            prev_close = next((b.close for b in reversed(daily) if b.start.date() < now.date()), None)
            instruments.append(
                {
                    "instrument_id": iid,
                    "symbol": instrument.symbol,
                    "price": price,
                    "change_pct": (price - prev_close) / prev_close * 100 if price and prev_close else None,
                    "regime": (analysis.get("regime") or {}).get("state"),
                    "regime_confidence": (analysis.get("regime") or {}).get("confidence"),
                    "trend": (analysis.get("regime") or {}).get("trend"),
                    "volatility": (analysis.get("regime") or {}).get("volatility"),
                    "delta_5m": flow.get("delta_5m"),
                    "buy_5m": flow.get("buy_5m"),
                    "sell_5m": flow.get("sell_5m"),
                    "simulated": self.simulated_covers(iid),
                }
            )
        hour = self.event_store.counts_since(now - timedelta(hours=1))
        by_category: dict[str, int] = {}
        for kinds in hour.values():
            for kind, n in kinds.items():
                cat = KINDS.get(kind, ("other",))[0]
                by_category[cat] = by_category.get(cat, 0) + n
        options = [c[2] for c in self._chains.values()][:2]
        up = [i for i in instruments if (i.get("change_pct") or 0) > 0]
        return {
            "at": now.isoformat(),
            "instruments": instruments,
            "breadth": len(up) / len(instruments) if instruments else None,
            "events_last_hour": by_category,
            "liquidity_events_last_hour": by_category.get("liquidity", 0),
            "recent_events": [e.to_dict() for e in self.events.recent(None, 15)],
            "options": [
                {
                    k: o.get(k)
                    for k in (
                        "underlying",
                        "expiry",
                        "spot",
                        "pcr_oi",
                        "atm_iv",
                        "max_pain",
                        "simulated",
                        "call_oi_change",
                        "put_oi_change",
                    )
                }
                for o in options
            ],
            "scanner": (self.scanner.last or {}).get("results", [])[:10],
            "health": self.health(),
        }


def describe(event: MarketEvent, before: list[dict[str, Any]]) -> str:
    """Plain-language explanation from the event's own numbers, used when no AI model is configured."""
    d = event.data
    meaning = KINDS.get(event.kind, ("", "", 0, ""))[3]
    parts = [f"{event.title}."]
    if event.kind in ("WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_PARTIAL"):
        parts.append(
            f"The level showed up to {d.get('peak_quantity', 0):,.0f} "
            f"for {d.get('duration_seconds', 0):.0f}s; "
            f"about {d.get('executed_near_level', 0):,.0f} traded there while it existed "
            f"({(d.get('execution_share') or 0):.0%} of what was removed)."
        )
        if d.get("confirmed_by"):
            parts.append(f"Other feeds showing it too: {', '.join(d['confirmed_by'])}.")
        parts.append("Trade attribution is estimated from volume changes between book updates.")
    elif meaning:
        parts.append(meaning + ".")
    if before:
        chain = " -> ".join(n["kind"].replace("_", " ").lower() for n in before[-4:])
        parts.append(f"It followed: {chain}.")
    return " ".join(parts)


def _json_object(text: str | None) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
