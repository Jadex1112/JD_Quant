"""Dhan live feeds: the v2 market feed, 20-level depth and 200-level depth.

Wire formats follow Dhan's official `dhanhq` SDK (marketfeed.py, fulldepth.py), all little-endian:

- **Market feed** `wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2`, JSON subscribe
  `{"RequestCode": 21, "InstrumentCount": n, "InstrumentList": [...]}` (Full mode, 100 per message).
  Packets start with an 8-byte header `<BHBI` (response code, length, segment, security id). The Full
  packet (code 8, 162 bytes) carries LTP, LTQ, LTT, volume, total buy/sell quantity, OI and 5 levels.
- **20-level depth** `wss://depth-api-feed.dhan.co/twentydepth?...`, subscribe with RequestCode 23 (up to
  50 instruments). Bid (code 41) and ask (code 51) packets arrive separately: a 12-byte header `<hBBiI`
  then 20 rows of `<dII` (price, quantity, orders).
- **200-level depth** `wss://full-depth-api.dhan.co/?...`, RequestCode 23 with one instrument per
  connection; the header's last field is the number of rows that follow.

Dhan stamps the last trade time as seconds since 1970 in Indian time; the SDK formats it as UTC to show
the IST clock time, so it is converted the same way here.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jdquant.connectivity.feeds.base import FeedAuthError, StreamingFeed
from jdquant.marketdata.book import BookSnapshot, Level, sort_side
from jdquant.markets.india import IST

MARKET_FEED = "wss://api-feed.dhan.co"
DEPTH_20 = "wss://depth-api-feed.dhan.co/twentydepth"
DEPTH_200 = "wss://full-depth-api.dhan.co/"
SEGMENT_NAMES = {
    0: "IDX_I",
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "MCX_COMM",
    7: "BSE_CURRENCY",
    8: "BSE_FNO",
}
DISCONNECT_REASONS = {
    805: "too many WebSocket connections",
    806: "data APIs not subscribed on this Dhan account",
    807: "access token expired",
    808: "invalid client id",
    809: "authentication failed",
}
AUTH_CODES = {806, 807, 808, 809}

_HEADER = struct.Struct("<BHBI")
_FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
_QUOTE = struct.Struct("<BHBIfHIfIIIffff")
_TICKER = struct.Struct("<BHBIfI")
_OI = struct.Struct("<BHBII")
_DEPTH5_ROW = struct.Struct("<IIHHff")
_DEPTH_HEADER = struct.Struct("<hBBiI")
_DEPTH_ROW = struct.Struct("<dII")


def dhan_time(epoch: int, fallback: datetime) -> datetime:
    """Dhan's trade time: IST wall-clock seconds since 1970."""
    if not epoch:
        return fallback
    return datetime.fromtimestamp(epoch, UTC).replace(tzinfo=IST)


@dataclass
class _Security:
    instrument_id: str
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    capacity: int = 5
    depth_source: str = "market"  # which feed owns the book: market (5 levels), 20 or 200
    last_price: float | None = None
    last_quantity: float | None = None
    last_time: datetime | None = None
    volume: float | None = None
    open_interest: float | None = None
    total_buy: float | None = None
    total_sell: float | None = None
    pending_bids: tuple[Level, ...] | None = None
    depth_at: float = 0.0  # monotonic time of the last deep-book update

    def deep_is_live(self) -> bool:
        return self.depth_source != "market" and time.monotonic() - self.depth_at < 5.0


@dataclass
class DhanBooks:
    """Book state shared by the market and depth connections of one Dhan account."""

    source: str
    by_security: dict[tuple[int, int], _Security] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, segment: int, security_id: int, instrument_id: str) -> _Security:
        with self.lock:
            sec = self.by_security.get((segment, security_id))
            if sec is None:
                sec = self.by_security[(segment, security_id)] = _Security(instrument_id)
            return sec

    def get(self, segment: int, security_id: int) -> _Security | None:
        return self.by_security.get((segment, security_id))

    def snapshot(self, sec: _Security, now: datetime) -> BookSnapshot:
        return BookSnapshot(
            sec.instrument_id,
            self.source,
            sec.last_time or now,
            now,
            bids=sec.bids,
            asks=sec.asks,
            last_price=sec.last_price,
            last_quantity=sec.last_quantity,
            volume=sec.volume,
            open_interest=sec.open_interest,
            total_buy_quantity=sec.total_buy,
            total_sell_quantity=sec.total_sell,
            capacity=sec.capacity,
        )


def parse_market_packets(data: bytes):
    """Split a market-feed message into (code, segment, security_id, packet) tuples."""
    offset = 0
    while offset + _HEADER.size <= len(data):
        code, length, segment, security_id = _HEADER.unpack_from(data, offset)
        known = {8: _FULL.size, 4: _QUOTE.size, 2: _TICKER.size, 6: _TICKER.size, 5: _OI.size, 50: 10}.get(
            code
        )
        if _HEADER.size <= length <= len(data) - offset:
            size = length  # the header's length field covers the whole packet
        else:
            size = min(known or len(data) - offset, len(data) - offset)
        yield code, segment, security_id, data[offset : offset + size]
        offset += size


def parse_depth_packets(data: bytes):
    """Split a 20/200-depth message into (code, segment, security_id, rows) tuples."""
    offset = 0
    while offset + _DEPTH_HEADER.size <= len(data):
        length, code, segment, security_id, extra = _DEPTH_HEADER.unpack_from(data, offset)
        if length <= 0 or offset + length > len(data):
            break
        body = data[offset + _DEPTH_HEADER.size : offset + length]
        rows = [
            _DEPTH_ROW.unpack_from(body, k)
            for k in range(0, len(body) - _DEPTH_ROW.size + 1, _DEPTH_ROW.size)
        ]
        yield code, segment, security_id, rows, extra
        offset += length


class _DhanSocket(StreamingFeed):
    def __init__(self, adapter, books: DhanBooks, hub, clock, *, connect=None, max_instruments=5000):
        super().__init__(adapter.venue, hub, clock, connect=connect, max_instruments=max_instruments)
        self.adapter = adapter
        self.books = books

    def _query(self) -> str:
        return f"token={self.adapter._token}&clientId={self.adapter.client_id}&authType=2"

    def _pairs(self, instrument_ids: list[str]) -> list[tuple[str, str, str]]:
        out = []
        for iid in instrument_ids:
            ref = self.adapter._refs.get(iid)
            if ref is not None:
                self.books.register(1, int(ref), iid)
                out.append(("NSE_EQ", str(ref), iid))
        return out

    @staticmethod
    def _disconnected(code: int) -> None:
        reason = DISCONNECT_REASONS.get(code, f"server closed the feed ({code})")
        if code in AUTH_CODES:
            raise FeedAuthError(f"Dhan: {reason}")
        raise ConnectionError(f"Dhan: {reason}")


class DhanMarketFeed(_DhanSocket):
    name = "market"

    def endpoint(self):
        if not self.adapter.is_ready():
            raise FeedAuthError("Dhan access token expired; rotate it on the connection")
        return f"{MARKET_FEED}?version=2&{self._query()}", None

    def subscribe(self, ws, instrument_ids):
        pairs = self._pairs(instrument_ids)
        for k in range(0, len(pairs), 100):
            batch = pairs[k : k + 100]
            ws.send(
                json.dumps(
                    {
                        "RequestCode": 21,
                        "InstrumentCount": len(batch),
                        "InstrumentList": [{"ExchangeSegment": s, "SecurityId": i} for s, i, _ in batch],
                    }
                )
            )

    def unsubscribe(self, ws, instrument_ids):
        pairs = self._pairs(instrument_ids)
        for k in range(0, len(pairs), 100):
            batch = pairs[k : k + 100]
            ws.send(
                json.dumps(
                    {
                        "RequestCode": 22,
                        "InstrumentCount": len(batch),
                        "InstrumentList": [{"ExchangeSegment": s, "SecurityId": i} for s, i, _ in batch],
                    }
                )
            )

    def handle(self, ws, message):
        if isinstance(message, str):
            return []
        now = self.clock.now()
        out = []
        for code, segment, security_id, packet in parse_market_packets(message):
            if code == 50:
                self._disconnected(
                    struct.unpack_from("<H", packet, _HEADER.size)[0] if len(packet) >= 10 else 0
                )
            sec = self.books.get(segment, security_id)
            if sec is None:
                continue
            if code == 8 and len(packet) >= _FULL.size:
                f = _FULL.unpack_from(packet)
                sec.last_price, sec.last_quantity = float(f[4]), float(f[5])
                sec.last_time = dhan_time(f[6], now)
                sec.volume, sec.total_sell, sec.total_buy = float(f[8]), float(f[9]), float(f[10])
                sec.open_interest = float(f[11])
                if not sec.deep_is_live():  # the 20/200-level socket owns the book while it updates
                    bids, asks = [], []
                    for k in range(5):
                        bq, aq, bo, ao, bp, ap = _DEPTH5_ROW.unpack_from(f[18], k * _DEPTH5_ROW.size)
                        bids.append(Level(round(bp, 4), float(bq), bo))
                        asks.append(Level(round(ap, 4), float(aq), ao))
                    sec.bids, sec.asks, sec.capacity = (
                        sort_side(bids, bid=True),
                        sort_side(asks, bid=False),
                        5,
                    )
            elif code == 4 and len(packet) >= _QUOTE.size:
                q = _QUOTE.unpack_from(packet)
                sec.last_price, sec.last_quantity = float(q[4]), float(q[5])
                sec.last_time = dhan_time(q[6], now)
                sec.volume, sec.total_sell, sec.total_buy = float(q[8]), float(q[9]), float(q[10])
            elif code == 2 and len(packet) >= _TICKER.size:
                t = _TICKER.unpack_from(packet)
                sec.last_price, sec.last_time = float(t[4]), dhan_time(t[5], now)
            elif code == 5 and len(packet) >= _OI.size:
                sec.open_interest = float(_OI.unpack_from(packet)[4])
                continue
            else:
                continue
            if sec.bids or sec.asks:
                out.append(self.books.snapshot(sec, now))
        return out


class DhanDepthFeed(_DhanSocket):
    """20 levels for up to 50 instruments, or 200 levels for a single instrument."""

    def __init__(self, adapter, books, hub, clock, *, levels: int = 20, connect=None):
        super().__init__(
            adapter, books, hub, clock, connect=connect, max_instruments=50 if levels == 20 else 1
        )
        self.levels = levels
        self.name = f"depth{levels}"

    def endpoint(self):
        if not self.adapter.is_ready():
            raise FeedAuthError("Dhan access token expired; rotate it on the connection")
        base = DEPTH_20 if self.levels == 20 else DEPTH_200
        return f"{base}?{self._query()}", None

    def subscribe(self, ws, instrument_ids):
        pairs = self._pairs(instrument_ids)
        for _, ref, iid in pairs:
            sec = self.books.register(1, int(ref), iid)
            sec.depth_source, sec.capacity = str(self.levels), self.levels
        if self.levels == 200:
            for s, i, _ in pairs[:1]:
                ws.send(json.dumps({"RequestCode": 23, "ExchangeSegment": s, "SecurityId": i}))
            return
        for k in range(0, len(pairs), 50):
            batch = pairs[k : k + 50]
            ws.send(
                json.dumps(
                    {
                        "RequestCode": 23,
                        "InstrumentCount": len(batch),
                        "InstrumentList": [{"ExchangeSegment": s, "SecurityId": i} for s, i, _ in batch],
                    }
                )
            )

    def unsubscribe(self, ws, instrument_ids):
        for _, ref, _iid in self._pairs(instrument_ids):
            sec = self.books.get(1, int(ref))
            if sec is not None and sec.depth_source == str(self.levels):
                sec.depth_source, sec.capacity = "market", 5
        pairs = self._pairs(instrument_ids)
        if pairs and self.levels == 20:
            ws.send(
                json.dumps(
                    {
                        "RequestCode": 24,
                        "InstrumentCount": len(pairs),
                        "InstrumentList": [{"ExchangeSegment": s, "SecurityId": i} for s, i, _ in pairs],
                    }
                )
            )

    def handle(self, ws, message):
        if isinstance(message, str):
            return []
        now = self.clock.now()
        out = []
        for code, segment, security_id, rows, extra in parse_depth_packets(message):
            if code == 50:
                self._disconnected(extra)
            sec = self.books.get(segment, security_id)
            if sec is None or code not in (41, 51):
                continue
            levels = [Level(round(p, 4), float(q), int(o)) for p, q, o in rows if p > 0 and q > 0]
            if code == 41:
                sec.pending_bids = sort_side(levels, bid=True)
                continue
            sec.asks = sort_side(levels, bid=False)
            sec.depth_at, sec.capacity = time.monotonic(), self.levels
            if sec.pending_bids is not None:
                sec.bids, sec.pending_bids = sec.pending_bids, None
            out.append(self.books.snapshot(sec, now))
        return out
