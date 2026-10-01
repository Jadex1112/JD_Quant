"""The market-data hub: one place every source publishes to, and every analytic reads from.

Broker WebSocket feeds and REST depth polls publish `BookSnapshot`s here. The hub:

- keeps the latest book of each (instrument, source) pair separately; sources are never merged;
- infers the trading between consecutive snapshots of a source (`Tick`);
- tracks each source's health: messages, last message, and the delay between the exchange timestamp
  and receipt;
- forwards the top of book to the trading engine's price cache from one source per instrument
  (the instrument's primary source, or any fresh one when the primary goes quiet), so a lagging second
  feed can never move the prices orders are checked against;
- hands books and ticks to the listeners (order-book intelligence, order flow, recorder, data quality)
  on its own worker thread, so slow analytics never hold up a socket.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from jdquant.core.clock import Clock
from jdquant.marketdata.book import BookSnapshot, Tick, infer_tick

log = logging.getLogger(__name__)

PRIMARY_QUIET = timedelta(seconds=5)  # use another source for prices once the primary is this quiet


@dataclass
class SourceStats:
    source: str
    kind: str = "poll"  # "stream" for WebSocket feeds, "poll" for REST depth polling
    connected: bool = False
    messages: int = 0
    books: int = 0
    ticks: int = 0
    rejected: int = 0  # invalid (crossed, unsorted) snapshots discarded
    dropped: int = 0  # discarded because the hub fell behind
    reconnects: int = 0
    auth_failures: int = 0
    last_message: datetime | None = None
    last_exchange_ts: datetime | None = None
    lag_ms: float | None = None  # received minus exchange time, smoothed
    last_error: str | None = None
    instruments: set[str] = field(default_factory=set)

    def to_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "connected": self.connected,
            "messages": self.messages,
            "books": self.books,
            "ticks": self.ticks,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "reconnects": self.reconnects,
            "auth_failures": self.auth_failures,
            "last_message": self.last_message.isoformat() if self.last_message else None,
            "seconds_since_message": (now - self.last_message).total_seconds() if self.last_message else None,
            "lag_ms": None if self.lag_ms is None else round(self.lag_ms, 1),
            "last_error": self.last_error,
            "instruments": sorted(self.instruments),
        }


BookListener = Callable[[BookSnapshot, BookSnapshot | None], None]
TickListener = Callable[[Tick], None]


class MarketDataHub:
    def __init__(
        self,
        clock: Clock,
        *,
        forward_quote: Callable[[Any], None] | None = None,
        primary_for: Callable[[str], str | None] | None = None,
        synchronous: bool = True,
        max_queue: int = 20_000,
    ):
        self._clock = clock
        self.forward_quote = forward_quote
        self.primary_for = primary_for or (lambda instrument_id: None)
        self._books: dict[str, dict[str, BookSnapshot]] = defaultdict(dict)
        self._stats: dict[str, SourceStats] = {}
        self._lock = threading.RLock()
        self._dispatch = threading.Lock()
        self.book_listeners: list[BookListener] = []
        self.tick_listeners: list[TickListener] = []
        self._synchronous = synchronous
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ------------------------------------------------------------------------------------

    def start(self) -> None:
        """Process snapshots on a worker thread from now on (the default is inline, for tests)."""
        if self._thread is None:
            self._synchronous = False
            self._thread = threading.Thread(target=self._loop, name="market-data-hub", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(snapshot)
            except Exception:
                log.exception("market data processing failed")

    def drain(self) -> None:
        """Process everything queued (tests and shutdown)."""
        while True:
            try:
                snapshot = self._queue.get_nowait()
            except queue.Empty:
                return
            self._process(snapshot)

    # ---- sources --------------------------------------------------------------------------------------

    def source(self, name: str, kind: str | None = None) -> SourceStats:
        with self._lock:
            stats = self._stats.get(name)
            if stats is None:
                stats = self._stats[name] = SourceStats(name, kind or "poll")
            elif kind is not None:
                stats.kind = kind
            return stats

    def set_connected(self, name: str, connected: bool, error: str | None = None, *, kind: str = "stream"):
        stats = self.source(name, kind)
        with self._lock:
            if connected and not stats.connected and stats.messages:
                stats.reconnects += 1
            stats.connected = connected
            if error is not None:
                stats.last_error = error

    def record_error(self, name: str, error: str, *, auth: bool = False) -> None:
        stats = self.source(name)
        with self._lock:
            stats.last_error = error
            if auth:
                stats.auth_failures += 1

    def sources(self) -> list[dict[str, Any]]:
        now = self._clock.now()
        with self._lock:
            return [s.to_dict(now) for s in sorted(self._stats.values(), key=lambda s: s.source)]

    # ---- publishing -----------------------------------------------------------------------------------

    def publish(self, snapshot: BookSnapshot) -> None:
        """Accept a snapshot from any thread."""
        stats = self.source(snapshot.source)
        with self._lock:
            stats.messages += 1
            stats.last_message = snapshot.received_ts
        if self._synchronous:
            self._process(snapshot)
            return
        try:
            self._queue.put_nowait(snapshot)
        except queue.Full:
            with self._lock:
                stats.dropped += 1

    def _process(self, snapshot: BookSnapshot) -> None:
        stats = self.source(snapshot.source)
        if not snapshot.is_valid():
            with self._lock:
                stats.rejected += 1
            return
        with self._lock:
            previous = self._books[snapshot.instrument_id].get(snapshot.source)
            if previous is not None and snapshot.exchange_ts < previous.exchange_ts - timedelta(seconds=1):
                stats.rejected += 1  # out of order
                return
            self._books[snapshot.instrument_id][snapshot.source] = snapshot
            stats.books += 1
            stats.instruments.add(snapshot.instrument_id)
            stats.last_exchange_ts = snapshot.exchange_ts
            lag = (snapshot.received_ts - snapshot.exchange_ts).total_seconds() * 1000
            if -60_000 < lag < 3_600_000:  # ignore clock nonsense (e.g. a closed market's old stamp)
                stats.lag_ms = lag if stats.lag_ms is None else 0.8 * stats.lag_ms + 0.2 * lag
            forward = self._should_forward(snapshot)
        tick = infer_tick(previous, snapshot)
        if tick is not None:
            with self._lock:
                stats.ticks += 1
        with self._dispatch:
            for listener in list(self.book_listeners):
                try:
                    listener(snapshot, previous)
                except Exception:
                    log.exception("book listener failed")
            if tick is not None:
                for listener in list(self.tick_listeners):
                    try:
                        listener(tick)
                    except Exception:
                        log.exception("tick listener failed")
        if forward and self.forward_quote is not None:
            quote = snapshot.quote()
            if quote is not None:
                try:
                    self.forward_quote(quote)
                except Exception:
                    log.exception("forwarding a quote failed")

    def _should_forward(self, snapshot: BookSnapshot) -> bool:
        primary = self.primary_for(snapshot.instrument_id)
        if primary is None or primary == snapshot.source:
            return True
        current = self._books[snapshot.instrument_id].get(primary)
        return current is None or snapshot.received_ts - current.received_ts > PRIMARY_QUIET

    # ---- reading --------------------------------------------------------------------------------------

    def books(self, instrument_id: str) -> dict[str, BookSnapshot]:
        with self._lock:
            return dict(self._books.get(instrument_id, {}))

    def book(self, instrument_id: str, source: str | None = None) -> BookSnapshot | None:
        """One source's book, or the freshest book from any source."""
        books = self.books(instrument_id)
        if source is not None:
            return books.get(source)
        return max(books.values(), key=lambda b: b.received_ts, default=None)

    def instruments(self) -> list[str]:
        with self._lock:
            return sorted(self._books)
