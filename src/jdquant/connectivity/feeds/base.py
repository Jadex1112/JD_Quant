"""Broker WebSocket feeds: one thread per connection, reconnecting with backoff, publishing to the hub.

Each feed translates its broker's wire format into `BookSnapshot`s stamped with the broker's venue as
the source, the same name the REST depth poller uses, so the hub, the order-book engine and the data
quality checks see one continuous source whether a price came over the socket or over REST. While a
feed is connected and receiving an instrument, the poller stops polling it; when the feed drops, REST
polling takes over again until the socket is back.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from jdquant.core.clock import Clock
from jdquant.marketdata.book import BookSnapshot
from jdquant.marketdata.hub import MarketDataHub

log = logging.getLogger(__name__)


class FeedAuthError(Exception):
    """The broker refused the credentials: back off longer, the user may need to sign in again."""


def default_connect(url: str, headers: dict[str, str] | None = None):
    from websockets.sync.client import connect

    return connect(url, additional_headers=headers or None, open_timeout=15, max_size=2**24, ping_interval=20)


class StreamingFeed:
    """A long-lived WebSocket to one broker endpoint, subscribed to a set of instruments."""

    name = "feed"

    def __init__(
        self,
        source: str,
        hub: MarketDataHub,
        clock: Clock,
        *,
        connect: Callable[..., Any] | None = None,
        max_instruments: int = 5000,
    ):
        self.source = source
        self.hub = hub
        self.clock = clock
        self._connect = connect or default_connect
        self.max_instruments = max_instruments
        self._wanted: set[str] = set()
        self._subscribed: set[str] = set()
        self._fresh: dict[str, float] = {}  # instrument -> monotonic time of its last update
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws = None
        self.connected = False
        self.last_error: str | None = None
        self.reconnects = 0

    # ---- control --------------------------------------------------------------------------------------

    def set_instruments(self, instrument_ids: Iterable[str]) -> None:
        with self._lock:
            self._wanted = set(list(instrument_ids)[: self.max_instruments])

    def wanted(self) -> set[str]:
        with self._lock:
            return set(self._wanted)

    def covers(self, instrument_id: str, within: float = 15.0) -> bool:
        """True while this feed is connected and has updated the instrument recently."""
        last = self._fresh.get(instrument_id)
        return self.connected and last is not None and time.monotonic() - last <= within

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name=f"feed-{self.source}-{self.name}", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- the loop -------------------------------------------------------------------------------------

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                url, headers = self.endpoint()
                with self._connect(url, headers) as ws:
                    self._ws = ws
                    self._subscribed = set()
                    self.on_open(ws)
                    self.connected = True
                    self.hub.set_connected(self.source, True)
                    backoff = 1.0
                    while not self._stop.is_set():
                        self._sync(ws)
                        self.heartbeat(ws)
                        try:
                            message = ws.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        for book in self.handle(ws, message):
                            self._fresh[book.instrument_id] = time.monotonic()
                            self.hub.publish(book)
            except FeedAuthError as exc:
                self.last_error = str(exc)
                self.hub.record_error(self.source, f"{self.name}: {exc}", auth=True)
                backoff = max(backoff, 60.0)
            except Exception as exc:  # network, protocol, closed socket: reconnect
                if not self._stop.is_set():
                    self.last_error = str(exc) or type(exc).__name__
                    self.hub.record_error(self.source, f"{self.name}: {self.last_error}")
                    log.info("%s %s feed dropped: %s", self.source, self.name, self.last_error)
            finally:
                self._ws = None
                if self.connected:
                    self.reconnects += 1
                self.connected = False
                self._subscribed = set()
                self.hub.set_connected(self.source, False)
            self._stop.wait(backoff)
            backoff = min(60.0, backoff * 2)

    def _sync(self, ws) -> None:
        wanted = self.wanted()
        added, removed = wanted - self._subscribed, self._subscribed - wanted
        if removed:
            self.unsubscribe(ws, sorted(removed))
            self._subscribed -= removed
        if added:
            self.subscribe(ws, sorted(added))
            self._subscribed |= added

    # ---- protocol hooks -------------------------------------------------------------------------------

    def endpoint(self) -> tuple[str, dict[str, str] | None]:
        raise NotImplementedError

    def on_open(self, ws) -> None:
        """Authenticate (when the protocol does it in-band) before subscribing."""

    def subscribe(self, ws, instrument_ids: list[str]) -> None:
        raise NotImplementedError

    def unsubscribe(self, ws, instrument_ids: list[str]) -> None:
        """Most feeds tolerate leftover subscriptions until the next reconnect."""

    def heartbeat(self, ws) -> None:
        """Protocol-level keep-alive, when the broker wants more than WebSocket pings."""

    def handle(self, ws, message: bytes | str) -> list[BookSnapshot]:
        raise NotImplementedError
