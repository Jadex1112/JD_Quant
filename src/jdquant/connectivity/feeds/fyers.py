"""Fyers live feed: the HSM data socket with symbol updates and 5-level market depth.

The binary protocol follows Fyers' official `fyers-apiv3` SDK (FyersWebsocket/data_ws.py):

- Connect to `wss://socket.fyers.in/hsm/v1-5/prod`, send an authentication message carrying the
  `hsm_key` from the access token's JWT payload, then switch the channel to full mode.
- Symbols are converted to HSM topics through `POST /data/symbol-token`: `sf|nse_cm|<token>` for
  symbol updates (LTP, volume, last traded quantity and time, total buy/sell quantity, OI) and
  `dp|nse_cm|<token>` for the five-level book.
- Data messages (type 6) hold snapshots (83), full updates (85) and lite updates (76) of integer fields
  scaled by `10**precision * multiplier`. When the server asks for acknowledgements, one is sent after
  every `ack_count` data messages. A three-byte ping goes out every 10 seconds.
"""

from __future__ import annotations

import base64
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jdquant.connectivity.feeds.base import FeedAuthError, StreamingFeed
from jdquant.marketdata.book import BookSnapshot, Level, sort_side

SOCKET = "wss://socket.fyers.in/hsm/v1-5/prod"
SYMBOL_TOKEN_API = "https://api-t1.fyers.in/data/symbol-token"
SOURCE_TAG = "PythonSDK-3.0.9"
CHANNEL = 11
SEGMENTS = {
    "1010": "nse_cm",
    "1011": "nse_fo",
    "1120": "mcx_fo",
    "1210": "bse_cm",
    "1012": "cde_fo",
    "1211": "bse_fo",
    "1212": "bcs_fo",
    "1020": "nse_com",
}
DATA_VAL = [
    "ltp",
    "vol_traded_today",
    "last_traded_time",
    "exch_feed_time",
    "bid_size",
    "ask_size",
    "bid_price",
    "ask_price",
    "last_traded_qty",
    "tot_buy_qty",
    "tot_sell_qty",
    "avg_trade_price",
    "OI",
    "low_price",
    "high_price",
    "Yhigh",
    "Ylow",
    "lower_ckt",
    "upper_ckt",
    "open_price",
    "prev_close_price",
    "type",
    "symbol",
]
DEPTH_VAL = (
    [f"bid_price{i}" for i in range(1, 6)]
    + [f"ask_price{i}" for i in range(1, 6)]
    + [f"bid_size{i}" for i in range(1, 6)]
    + [f"ask_size{i}" for i in range(1, 6)]
    + [f"bid_order{i}" for i in range(1, 6)]
    + [f"ask_order{i}" for i in range(1, 6)]
)
SCALED = {
    "ltp",
    "bid_price",
    "ask_price",
    "avg_trade_price",
    "low_price",
    "high_price",
    "open_price",
    "prev_close_price",
}
EMPTY = -2147483648


def hsm_key(access_token: str) -> tuple[str, int]:
    """The HSM key and expiry from the Fyers access token (a JWT); raises FeedAuthError when unusable."""
    token = access_token.split(":")[-1]
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "==="))
        return str(data["hsm_key"]), int(data.get("exp", 0))
    except (IndexError, KeyError, ValueError, TypeError):
        raise FeedAuthError("the Fyers access token has no HSM key; sign in again") from None


def auth_message(key: str, mode: str = "P") -> bytes:
    fields = [(1, key.encode()), (2, mode.encode()), (3, bytes([1])), (4, SOURCE_TAG.encode())]
    body = bytearray([1, len(fields)])
    for fid, value in fields:
        body += bytes([fid]) + struct.pack("!H", len(value)) + value
    return struct.pack("!H", len(body)) + bytes(body)


def full_mode_message(channel: int = CHANNEL) -> bytes:
    data = bytearray(struct.pack(">H", 0)) + bytes([12, 2])
    data += bytes([1]) + struct.pack(">H", 8) + struct.pack(">Q", 1 << channel)
    data += bytes([2]) + struct.pack(">H", 1) + bytes([70])
    return bytes(data)


def subscription_message(
    topics: list[str], access_token: str, request_type: int = 4, channel: int = CHANNEL
) -> bytes:
    """Subscribe (4) or unsubscribe (5). The length prefix is computed exactly as the official SDK does
    (it counts the access token and SDK tag, which are not in the message); the server accepts it."""
    scrips = bytearray(struct.pack(">H", len(topics)))
    for topic in topics:
        raw = topic.encode("ascii")
        scrips += bytes([len(raw)]) + raw
    declared = 18 + len(scrips) + len(access_token) + len(SOURCE_TAG)
    body = bytearray([request_type, 2]) + bytes([1]) + struct.pack(">H", len(scrips)) + scrips
    body += bytes([2]) + struct.pack(">H", 1) + bytes([channel])
    return struct.pack(">H", declared) + bytes(body)


def ack_message(message_number: int) -> bytes:
    return struct.pack(">H", 9) + bytes([3, 1, 1]) + struct.pack(">H", 4) + struct.pack(">I", message_number)


PING = bytes([0, 1, 11])


@dataclass
class _Topic:
    topic: str
    instrument_id: str
    kind: str  # sf or dp
    values: dict[str, int] = field(default_factory=dict)
    multiplier: int = 1
    precision: int = 2

    def scaled(self, name: str) -> float | None:
        v = self.values.get(name)
        if v is None:
            return None
        return v / ((10**self.precision) * (self.multiplier or 1))


class FyersFeed(StreamingFeed):
    name = "data"

    def __init__(self, adapter, hub, clock, *, connect=None):
        super().__init__(adapter.venue, hub, clock, connect=connect, max_instruments=2500)
        self.adapter = adapter
        self._topics: dict[str, _Topic] = {}  # topic name -> state
        self._by_id: dict[int, _Topic] = {}  # server topic id -> state
        self._by_instrument: dict[str, dict[str, _Topic]] = {}
        self.ack_count = 0
        self._updates = 0
        self._last_ping = 0.0

    def endpoint(self):
        token = self.adapter.access_token
        if not token:
            raise FeedAuthError("sign in to Fyers first")
        _, expires = hsm_key(token)
        if expires and expires < time.time():
            raise FeedAuthError("the Fyers session expired; sign in again")
        return SOCKET, None

    def on_open(self, ws):
        key, _ = hsm_key(self.adapter.access_token)
        self._topics, self._by_id, self._by_instrument = {}, {}, {}
        ws.send(auth_message(key))
        ws.send(full_mode_message())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                message = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            if isinstance(message, (bytes, bytearray)) and len(message) >= 3 and message[2] == 1:
                self._auth_response(bytes(message))
                return
        raise FeedAuthError("Fyers did not confirm authentication")

    def _auth_response(self, data: bytes) -> None:
        offset = 5
        length = struct.unpack("!H", data[offset : offset + 2])[0]
        offset += 2
        verdict = data[offset : offset + length].decode("utf-8", "ignore")
        offset += length
        if verdict != "K":
            raise FeedAuthError("Fyers rejected the feed authentication")
        offset += 1
        if offset + 2 <= len(data):
            length = struct.unpack("!H", data[offset : offset + 2])[0]
            offset += 2
            if length == 4 and offset + 4 <= len(data):
                self.ack_count = struct.unpack(">I", data[offset : offset + 4])[0]

    def _hsm_topics(self, instrument_ids: list[str]) -> dict[str, tuple[str, str]]:
        """{topic: (instrument_id, kind)} via Fyers' symbol-token lookup."""
        token = str(self.adapter.access_token).split(":")[-1]
        response = self.adapter._http.post(
            SYMBOL_TOKEN_API, json={"symbols": instrument_ids}, headers={"Authorization": token}
        )
        body = response.json()
        if body.get("s") != "ok":
            raise ConnectionError(f"Fyers symbol-token lookup failed: {body.get('message')}")
        out = {}
        for symbol, fytoken in (body.get("validSymbol") or {}).items():
            segment = SEGMENTS.get(str(fytoken)[:4])
            if segment is None or symbol.endswith("-INDEX"):
                continue
            exch_token = str(fytoken)[10:]
            out[f"sf|{segment}|{exch_token}"] = (symbol, "sf")
            out[f"dp|{segment}|{exch_token}"] = (symbol, "dp")
        return out

    def subscribe(self, ws, instrument_ids):
        topics = self._hsm_topics(instrument_ids)
        for topic, (iid, kind) in topics.items():
            state = _Topic(topic, iid, kind)
            self._topics[topic] = state
            self._by_instrument.setdefault(iid, {})[kind] = state
        names = list(topics)
        for k in range(0, len(names), 1500):
            ws.send(subscription_message(names[k : k + 1500], self._token_string()))

    def unsubscribe(self, ws, instrument_ids):
        names = [
            t for iid in instrument_ids for t in (s.topic for s in self._by_instrument.pop(iid, {}).values())
        ]
        for k in range(0, len(names), 1500):
            ws.send(subscription_message(names[k : k + 1500], self._token_string(), request_type=5))

    def _token_string(self) -> str:
        return f"{self.adapter.app_id}:{self.adapter.access_token}"

    def heartbeat(self, ws):
        now = time.monotonic()
        if now - self._last_ping >= 10:
            self._last_ping = now
            ws.send(PING)

    def handle(self, ws, message):
        if not isinstance(message, (bytes, bytearray)) or len(message) < 3:
            return []
        data = bytes(message)
        if data[2] != 6:
            return []
        if self.ack_count > 0 and len(data) >= 7:
            self._updates += 1
            if self._updates >= self.ack_count:
                self._updates = 0
                ws.send(ack_message(struct.unpack(">I", data[3:7])[0]))
        touched = self._parse(data)
        now = self.clock.now()
        books = []
        for iid in touched:
            book = self._book(iid, now)
            if book is not None:
                books.append(book)
        return books

    def _parse(self, data: bytes) -> set[str]:
        touched: set[str] = set()
        if len(data) < 9:
            return touched
        count = struct.unpack("!H", data[7:9])[0]
        offset = 9
        for _ in range(count):
            if offset >= len(data):
                break
            kind = data[offset]
            offset += 1
            if kind == 83:  # snapshot: topic id, name, all fields, scale, strings
                topic_id = struct.unpack("<H", data[offset : offset + 2])[0]
                offset += 2
                name_len = data[offset]
                offset += 1
                name = data[offset : offset + name_len].decode("utf-8", "ignore")
                offset += name_len
                n = data[offset]
                offset += 1
                values = struct.unpack(f">{n}i", data[offset : offset + 4 * n])
                offset += 4 * n + 2
                multiplier = struct.unpack(">H", data[offset : offset + 2])[0]
                offset += 2
                precision = data[offset]
                offset += 1
                for _ in range(3):  # exchange, exchange token, symbol
                    length = data[offset]
                    offset += 1 + length
                state = self._topics.get(name)
                if state is None:
                    continue
                names = DEPTH_VAL if state.kind == "dp" else DATA_VAL
                state.values = {names[i]: v for i, v in enumerate(values) if i < len(names) and v != EMPTY}
                state.multiplier, state.precision = multiplier, precision
                self._by_id[topic_id] = state
                touched.add(state.instrument_id)
            elif kind == 85:  # full update: topic id, all fields
                topic_id = struct.unpack("<H", data[offset : offset + 2])[0]
                offset += 2
                n = data[offset]
                offset += 1
                values = struct.unpack(f">{n}i", data[offset : offset + 4 * n])
                offset += 4 * n
                state = self._by_id.get(topic_id)
                if state is None:
                    continue
                names = DEPTH_VAL if state.kind == "dp" else DATA_VAL
                for i, v in enumerate(values):
                    if i < len(names) and v != EMPTY:
                        state.values[names[i]] = v
                touched.add(state.instrument_id)
            elif kind == 76:  # lite update: topic id, LTP
                topic_id = struct.unpack("<H", data[offset : offset + 2])[0]
                offset += 2
                value = struct.unpack(">i", data[offset : offset + 4])[0]
                offset += 4
                state = self._by_id.get(topic_id)
                if state is not None and value != EMPTY:
                    state.values["ltp"] = value
                    touched.add(state.instrument_id)
            else:
                break  # unknown record type: the rest of the frame cannot be parsed
        return touched

    def _book(self, instrument_id: str, now: datetime) -> BookSnapshot | None:
        states = self._by_instrument.get(instrument_id, {})
        sf, dp = states.get("sf"), states.get("dp")
        bids, asks = [], []
        if dp is not None and dp.values:
            for i in range(1, 6):
                bp, ap = dp.scaled(f"bid_price{i}"), dp.scaled(f"ask_price{i}")
                if bp:
                    bids.append(
                        Level(
                            round(bp, 6),
                            float(dp.values.get(f"bid_size{i}", 0)),
                            dp.values.get(f"bid_order{i}"),
                        )
                    )
                if ap:
                    asks.append(
                        Level(
                            round(ap, 6),
                            float(dp.values.get(f"ask_size{i}", 0)),
                            dp.values.get(f"ask_order{i}"),
                        )
                    )
        elif sf is not None and sf.values.get("bid_price") and sf.values.get("ask_price"):
            bids = [Level(round(sf.scaled("bid_price"), 6), float(sf.values.get("bid_size", 0)))]
            asks = [Level(round(sf.scaled("ask_price"), 6), float(sf.values.get("ask_size", 0)))]
        if not bids and not asks:
            return None
        at = now
        if sf is not None:
            stamp = sf.values.get("exch_feed_time") or sf.values.get("last_traded_time")
            if stamp:
                at = datetime.fromtimestamp(stamp, UTC)
        v = sf.values if sf is not None else {}
        return BookSnapshot(
            instrument_id,
            self.source,
            at,
            now,
            bids=sort_side(bids, bid=True),
            asks=sort_side(asks, bid=False),
            last_price=sf.scaled("ltp") if sf is not None else None,
            last_quantity=float(v["last_traded_qty"]) if "last_traded_qty" in v else None,
            volume=float(v["vol_traded_today"]) if "vol_traded_today" in v else None,
            open_interest=float(v["OI"]) if "OI" in v else None,
            total_buy_quantity=float(v["tot_buy_qty"]) if "tot_buy_qty" in v else None,
            total_sell_quantity=float(v["tot_sell_qty"]) if "tot_sell_qty" in v else None,
            capacity=5 if dp is not None else 1,
        )
