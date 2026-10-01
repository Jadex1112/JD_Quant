"""Runs the broker WebSocket feeds for the instruments under depth watch.

Every connected broker that can stream an instrument gets it, so the same book is observed through
several independent feeds (the data-quality engine compares them; nothing adds them up). Dhan also
opens 20-level depth for up to 50 instruments and 200-level depth for up to five chosen instruments.
"""

from __future__ import annotations

import logging
from typing import Any

from jdquant.connectivity.feeds.base import StreamingFeed
from jdquant.connectivity.feeds.dhan import DhanBooks, DhanDepthFeed, DhanMarketFeed
from jdquant.connectivity.feeds.fyers import FyersFeed
from jdquant.connectivity.feeds.neo import NeoFeed
from jdquant.core.clock import Clock
from jdquant.marketdata.hub import MarketDataHub

log = logging.getLogger(__name__)

STREAMING_VENUES = ("DHAN", "FYERS", "KOTAKNEO")
MAX_DEEP = 5


class FeedManager:
    def __init__(self, connections, hub: MarketDataHub, clock: Clock, *, connect=None):
        self._connections = connections
        self._hub = hub
        self._clock = clock
        self._connect = connect
        self.enabled = True
        self.deep: list[str] = []  # instruments that get Dhan's 200-level book
        self._feeds: dict[str, list[StreamingFeed]] = {}

    def _knows(self, adapter, instrument_id: str) -> bool:
        if adapter.venue == "FYERS":
            return instrument_id.split(":")[0] in ("NSE", "BSE", "MCX")
        return instrument_id in getattr(adapter, "_refs", {})

    def _build(self, adapter) -> list[StreamingFeed]:
        if adapter.venue == "DHAN":
            books = DhanBooks(adapter.venue)
            feeds: list[StreamingFeed] = [
                DhanMarketFeed(adapter, books, self._hub, self._clock, connect=self._connect),
                DhanDepthFeed(adapter, books, self._hub, self._clock, levels=20, connect=self._connect),
            ]
            feeds += [
                DhanDepthFeed(adapter, books, self._hub, self._clock, levels=200, connect=self._connect)
                for _ in range(MAX_DEEP)
            ]
            return feeds
        if adapter.venue == "FYERS":
            return [FyersFeed(adapter, self._hub, self._clock, connect=self._connect)]
        if adapter.venue == "KOTAKNEO":
            return [NeoFeed(adapter, self._hub, self._clock, connect=self._connect)]
        return []

    def sync(self, instruments: list[str]) -> None:
        """Start, retarget or stop feeds so every streaming broker covers the watched instruments."""
        live = set()
        if self.enabled and instruments:
            for connection_id, adapter in list(self._connections.adapters.items()):
                if adapter.venue not in STREAMING_VENUES or not adapter.is_ready():
                    continue
                relevant = [i for i in instruments if self._knows(adapter, i)]
                if not relevant:
                    continue
                live.add(connection_id)
                feeds = self._feeds.get(connection_id)
                if feeds is None:
                    feeds = self._feeds[connection_id] = self._build(adapter)
                self._assign(adapter, feeds, relevant)
        for connection_id in list(self._feeds):
            if connection_id not in live:
                for feed in self._feeds.pop(connection_id):
                    feed.stop()

    def _assign(self, adapter, feeds: list[StreamingFeed], relevant: list[str]) -> None:
        if adapter.venue == "DHAN":
            market, depth20, *deep_feeds = feeds
            deep = [i for i in self.deep if i in relevant][:MAX_DEEP]
            market.set_instruments(relevant)
            depth20.set_instruments([i for i in relevant if i not in deep][:50])
            for k, feed in enumerate(deep_feeds):
                feed.set_instruments([deep[k]] if k < len(deep) else [])
            targets = [market, depth20] + [f for k, f in enumerate(deep_feeds) if k < len(deep)]
            for feed in feeds:
                if feed in targets and feed.wanted():
                    feed.start()
            return
        for feed in feeds:
            feed.set_instruments(relevant)
            feed.start()

    def covers(self, instrument_id: str) -> bool:
        return any(feed.covers(instrument_id) for feeds in self._feeds.values() for feed in feeds)

    def status(self) -> list[dict[str, Any]]:
        out = []
        for connection_id, feeds in self._feeds.items():
            for feed in feeds:
                if not feed.wanted() and not feed.running:
                    continue
                out.append(
                    {
                        "connection_id": connection_id,
                        "source": feed.source,
                        "feed": feed.name,
                        "running": feed.running,
                        "connected": feed.connected,
                        "instruments": sorted(feed.wanted()),
                        "reconnects": feed.reconnects,
                        "last_error": feed.last_error,
                    }
                )
        return out

    def stop(self) -> None:
        for feeds in self._feeds.values():
            for feed in feeds:
                feed.stop()
        self._feeds.clear()
