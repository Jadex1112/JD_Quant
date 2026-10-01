"""Kotak Neo SFeed: live market pictures with depth, in Kotak's `native_batch` binary format.

The protocol follows Kotak's official `kotakneoapi` SDK (websocket/feed/protocol.py and client.py):

- A JSON authentication frame with the client code as `user` and the session id as `auth`; the reply
  (message code 1117 or 1119) lists each exchange's price divider.
- Subscriptions are JSON frames, e.g. `{"event": "subscribeFullDepth", "inputtoken": "nse_cm|1333",
  "ack_symbol": true}`.
- Data frames are little-endian and batched: each packet starts with its `uint16` length and a 9-byte
  header (length, message code, exchange id, level, auction flag, sequence, bitmask length). Market
  pictures carry the last trade, volume, OI and total buy/sell quantity at fixed offsets, followed by
  depth rows of `<qii` (quantity, price, orders). Prices are integers divided by the exchange divider.
"""

from __future__ import annotations

import json
import struct
import time
from datetime import UTC, datetime

from jdquant.connectivity.feeds.base import FeedAuthError, StreamingFeed
from jdquant.marketdata.book import BookSnapshot, Level, sort_side

HEADER = struct.Struct("<HHbBBBB")
HEADER_SIZE = 9
MP_BODY = struct.Struct("<IqqqqqIIIIIqIIIIhiIdiIIIIIBI")
MP_FIXED_END = 144
DEPTH_ROW = struct.Struct("<qii")
MINI_BODY = struct.Struct("<IqIqIiiIBI")
AUTH_CODES = (1117, 1119)
EXCHANGES = {
    "nse_cm": 1,
    "nse_fo": 2,
    "cde_fo": 3,
    "nse_com": 4,
    "bse_cm": 5,
    "bse_fo": 6,
    "bse_cd": 7,
    "bse_co": 8,
    "mcx_fo": 9,
    "ncd_co": 10,
}
LEVEL_MINI, LEVEL_TOUCH, LEVEL_DEPTH, LEVEL_FULL = 1, 4, 8, 16
SKIP_CODES = (6511, 6521, 7207, 105, 104)  # market open/close, index, market status, closing auction


def split_batch(frame: bytes) -> list[bytes]:
    packets, offset = [], 0
    while offset + 2 <= len(frame):
        size = struct.unpack_from("<H", frame, offset)[0]
        if size < HEADER_SIZE or offset + size > len(frame):
            break
        packets.append(frame[offset : offset + size])
        offset += size
    return packets


def decode_market_picture(packet: bytes, divider: int) -> dict | None:
    """The fields the platform uses from one market-picture packet (None for other packets)."""
    if len(packet) < HEADER_SIZE:
        return None
    length, code, _exchange, level, _auction, _seq, _bitmask = HEADER.unpack_from(packet, 0)
    if code in SKIP_CODES:
        return None
    if level == LEVEL_MINI and len(packet) >= HEADER_SIZE + MINI_BODY.size:
        token, ltt, ltp, ltq, *_ = MINI_BODY.unpack_from(packet, HEADER_SIZE)
        return {"token": str(token), "ltp": ltp / divider, "ltq": ltq, "ltt": ltt, "buy": [], "sell": []}
    if level not in (2, LEVEL_TOUCH, LEVEL_DEPTH, LEVEL_FULL) or len(packet) < MP_FIXED_END:
        return None
    f = MP_BODY.unpack_from(packet, HEADER_SIZE)
    token, tbq, tsq, volume, ltt, _lut = f[0], f[1], f[2], f[3], f[4], f[5]
    ltp, ltq, buy_n, sell_n, oi = f[10], f[11], f[14], f[15], f[18]
    if level == LEVEL_TOUCH:
        buy_n = sell_n = 1
    rows, offset, limit = [], MP_FIXED_END, min(length, len(packet))
    for _ in range(buy_n + sell_n):
        if offset + DEPTH_ROW.size > limit:
            break
        rows.append(DEPTH_ROW.unpack_from(packet, offset))
        offset += DEPTH_ROW.size
    buy = [Level(p / divider, float(q), o) for q, p, o in rows[:buy_n] if p > 0 and q > 0]
    sell = [Level(p / divider, float(q), o) for q, p, o in rows[buy_n:] if p > 0 and q > 0]
    return {
        "token": str(token),
        "ltp": ltp / divider,
        "ltq": ltq,
        "ltt": ltt,
        "volume": volume,
        "tbq": tbq,
        "tsq": tsq,
        "oi": oi,
        "buy": buy,
        "sell": sell,
        "level": level,
    }


class NeoFeed(StreamingFeed):
    name = "sfeed"

    def __init__(self, adapter, hub, clock, *, connect=None, event: str = "subscribeFullDepth"):
        super().__init__(adapter.venue, hub, clock, connect=connect, max_instruments=3000)
        self.adapter = adapter
        self.event = event
        self._dividers: dict[int, int] = {}
        self._by_token: dict[str, str] = {}
        self._last: dict[str, dict] = {}

    def endpoint(self):
        creds = self.adapter.feed_credentials()
        self._creds = creds
        return creds["url"], None

    def on_open(self, ws):
        frame = {
            "user": self._creds["user"],
            "auth": self._creds["auth"],
            "format": "native_batch",
            "source": "NEOTRADEAPI",
            "platform": "Web",
            "version": "1.2.3",
            "sdk_version": 2,
            "sdk_date": "2026-08-07T09:41:17.667Z",
            "conn_req_time": int(time.time() * 1000),
            "sessionValidation": False,
        }
        ws.send(json.dumps(frame))
        for _ in range(10):
            try:
                raw = ws.recv(timeout=10)
            except TimeoutError:
                break
            if not isinstance(raw, str):
                continue
            data = json.loads(raw)
            if data.get("message_code") not in AUTH_CODES or data.get("format") == "native_fallback":
                raise FeedAuthError(f"Kotak Neo feed authentication failed: {data!r}"[:200])
            self._dividers = {
                int(info.get("value", EXCHANGES.get(name, 0))): int(info.get("divider", 100))
                for name, info in (data.get("exchanges") or {}).items()
                if isinstance(info, dict)
            }
            return
        raise FeedAuthError("Kotak Neo feed did not answer the authentication frame")

    def _tokens(self, instrument_ids: list[str]) -> list[str]:
        out = []
        for iid in instrument_ids:
            ref = self.adapter._refs.get(iid)
            if ref is None:
                continue
            token = ref.split("|")[1]
            self._by_token[token] = iid
            out.append(f"nse_cm|{token}")
        return out

    def subscribe(self, ws, instrument_ids):
        tokens = self._tokens(instrument_ids)
        for k in range(0, len(tokens), 200):
            ws.send(
                json.dumps(
                    {"event": self.event, "inputtoken": ",".join(tokens[k : k + 200]), "ack_symbol": True}
                )
            )

    def unsubscribe(self, ws, instrument_ids):
        tokens = self._tokens(instrument_ids)
        event = "unsubscribe" + self.event.removeprefix("subscribe")
        for k in range(0, len(tokens), 200):
            ws.send(json.dumps({"event": event, "inputtoken": ",".join(tokens[k : k + 200])}))

    def handle(self, ws, message):
        if isinstance(message, str):
            return []  # subscribe acknowledgements and other control frames
        now = self.clock.now()
        books = []
        for packet in split_batch(bytes(message)):
            exchange = struct.unpack_from("<b", packet, 4)[0]
            decoded = decode_market_picture(packet, self._dividers.get(exchange, 100))
            if decoded is None:
                continue
            iid = self._by_token.get(decoded["token"])
            if iid is None:
                continue
            state = self._last.setdefault(iid, {})
            if decoded["buy"] or decoded["sell"] or "buy" not in state:
                state["buy"], state["sell"] = decoded["buy"], decoded["sell"]
            for key in ("ltp", "ltq", "ltt", "volume", "tbq", "tsq", "oi", "level"):
                if decoded.get(key) is not None:
                    state[key] = decoded[key]
            at = datetime.fromtimestamp(state["ltt"], UTC) if state.get("ltt", 0) > 0 else now
            books.append(
                BookSnapshot(
                    iid,
                    self.source,
                    at,
                    now,
                    bids=sort_side(state["buy"], bid=True),
                    asks=sort_side(state["sell"], bid=False),
                    last_price=state.get("ltp") or None,
                    last_quantity=float(state["ltq"]) if state.get("ltq") else None,
                    volume=float(state["volume"]) if state.get("volume") is not None else None,
                    open_interest=float(state["oi"]) if state.get("oi") else None,
                    total_buy_quantity=float(state["tbq"]) if state.get("tbq") is not None else None,
                    total_sell_quantity=float(state["tsq"]) if state.get("tsq") is not None else None,
                    capacity=max(5, len(state["buy"]), len(state["sell"])),
                )
            )
        return books
