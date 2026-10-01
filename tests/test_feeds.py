"""Broker WebSocket feeds against local servers speaking each broker's wire format.

The frames here are packed from the layouts in the brokers' official SDKs (dhanhq, fyers-apiv3,
kotakneoapi), independently of the parser constants, so a transcription error in either shows up.
"""

import base64
import json
import struct
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from websockets.sync.client import connect as ws_connect
from websockets.sync.server import serve

from jdquant.connectivity.feeds.dhan import DhanBooks, DhanDepthFeed, DhanMarketFeed
from jdquant.connectivity.feeds.fyers import FyersFeed
from jdquant.connectivity.feeds.manager import FeedManager
from jdquant.connectivity.feeds.neo import NeoFeed
from jdquant.core.clock import SystemClock
from jdquant.marketdata.hub import MarketDataHub

SBIN = "NSE:SBIN-EQ"


class Server:
    """A local WebSocket server running `handler(ws)` per connection; records the URLs clients asked for."""

    def __init__(self, handler):
        self.handler = handler
        self.requested: list[str] = []
        self.received: list = []
        self._server = serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.socket.getsockname()[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _handle(self, ws):
        try:
            self.handler(ws, self)
        except Exception:
            pass

    def connect(self, url, headers=None):
        self.requested.append(url)
        return ws_connect(f"ws://127.0.0.1:{self.port}", open_timeout=5)

    def close(self):
        self._server.shutdown()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class DhanStub:
    venue = "DHAN"
    client_id = "1000000001"
    _token = "dhan-token"
    _refs = {SBIN: "3045"}

    def is_ready(self):
        return True


# ---- Dhan -----------------------------------------------------------------------------------------------


def dhan_full_packet(security_id=3045, ltp=812.5, volume=150_000):
    depth = b"".join(
        struct.pack(
            "<IIHHff", 100 * (k + 1), 120 * (k + 1), k + 1, k + 2, ltp - 0.05 * (k + 1), ltp + 0.05 * k
        )
        for k in range(5)
    )
    ltt = int(datetime(2026, 9, 28, 11, 2, 1, tzinfo=UTC).timestamp())  # 11:02:01 IST as Dhan encodes it
    return struct.pack(
        "<BHBIfHIfIIIIIIffff100s",
        8,
        162,
        1,
        security_id,
        ltp,
        25,
        ltt,
        811.9,
        volume,
        40_000,
        55_000,
        0,
        0,
        0,
        805.0,
        809.0,
        815.0,
        801.0,
        depth,
    )


def test_dhan_market_feed_full_packet():
    def handler(ws, server):
        sub = json.loads(ws.recv(timeout=5))
        server.received.append(sub)
        ws.send(dhan_full_packet())
        ws.send(dhan_full_packet(volume=150_600, ltp=812.55))
        time.sleep(1)

    server = Server(handler)
    hub = MarketDataHub(SystemClock())
    feed = DhanMarketFeed(DhanStub(), DhanBooks("DHAN"), hub, SystemClock(), connect=server.connect)
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: (b := hub.book(SBIN)) is not None and b.volume == 150_600)
        book = hub.book(SBIN)
        assert server.received[0] == {
            "RequestCode": 21,
            "InstrumentCount": 1,
            "InstrumentList": [{"ExchangeSegment": "NSE_EQ", "SecurityId": "3045"}],
        }
        assert "version=2" in server.requested[0] and "clientId=1000000001" in server.requested[0]
        assert book.last_price == pytest.approx(812.55) and book.last_quantity == 25
        assert book.total_buy_quantity == 55_000 and book.total_sell_quantity == 40_000
        assert len(book.bids) == 5 and book.bids[0].quantity == 100 and book.bids[0].orders == 1
        assert book.exchange_ts.hour == 11 and book.exchange_ts.utcoffset().total_seconds() == 19_800
        assert hub.sources()[0]["ticks"] == 1  # 600 shares inferred from the volume change
    finally:
        feed.stop()
        server.close()


def dhan_depth_message(code, security_id, prices, rows_field=0):
    body = b"".join(struct.pack("<dII", p, 1000 + k, 3 + k) for k, p in enumerate(prices))
    header = struct.pack("<hBBiI", 12 + len(body), code, 1, security_id, rows_field)
    return header + body


def test_dhan_twenty_and_two_hundred_level_depth():
    bids = [800.0 - 0.05 * k for k in range(20)]
    asks = [800.05 + 0.05 * k for k in range(20)]

    def handler(ws, server):
        server.received.append(json.loads(ws.recv(timeout=5)))
        ws.send(dhan_depth_message(41, 3045, bids) + dhan_depth_message(51, 3045, asks))
        time.sleep(1)

    server = Server(handler)
    hub = MarketDataHub(SystemClock())
    books = DhanBooks("DHAN")
    feed = DhanDepthFeed(DhanStub(), books, hub, SystemClock(), levels=20, connect=server.connect)
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: hub.book(SBIN) is not None)
        book = hub.book(SBIN)
        assert len(book.bids) == 20 and len(book.asks) == 20 and book.capacity == 20
        assert book.bids[0].price == 800.0 and book.asks[0].price == 800.05 and book.asks[19].orders == 22
        assert server.received[0]["RequestCode"] == 23 and "twentydepth" in server.requested[0]
    finally:
        feed.stop()
        server.close()

    deep_bids = [800.0 - 0.05 * k for k in range(200)]
    deep_asks = [800.05 + 0.05 * k for k in range(200)]

    def handler200(ws, server):
        server.received.append(json.loads(ws.recv(timeout=5)))
        ws.send(dhan_depth_message(41, 3045, deep_bids, 200))
        ws.send(dhan_depth_message(51, 3045, deep_asks, 200))
        time.sleep(1)

    server = Server(handler200)
    hub = MarketDataHub(SystemClock())
    feed = DhanDepthFeed(
        DhanStub(), DhanBooks("DHAN"), hub, SystemClock(), levels=200, connect=server.connect
    )
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: hub.book(SBIN) is not None)
        assert len(hub.book(SBIN).bids) == 200 and hub.book(SBIN).capacity == 200
        assert server.received[0] == {"RequestCode": 23, "ExchangeSegment": "NSE_EQ", "SecurityId": "3045"}
    finally:
        feed.stop()
        server.close()


def test_dhan_expired_token_disconnect_is_an_auth_failure():
    def handler(ws, server):
        ws.recv(timeout=5)
        ws.send(struct.pack("<BHBIH", 50, 10, 1, 0, 807))
        time.sleep(0.5)

    server = Server(handler)
    hub = MarketDataHub(SystemClock())
    feed = DhanMarketFeed(DhanStub(), DhanBooks("DHAN"), hub, SystemClock(), connect=server.connect)
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: hub.sources() and hub.sources()[0]["auth_failures"] == 1)
        assert "access token expired" in hub.sources()[0]["last_error"]
    finally:
        feed.stop()
        server.close()


# ---- Fyers ----------------------------------------------------------------------------------------------


def fyers_token():
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'hsm_key': 'hsm-abc', 'exp': int(time.time()) + 3600})}.sig"


class FyersStub:
    venue = "FYERS"
    app_id = "APP-100"

    def __init__(self):
        self.access_token = fyers_token()

        def handle(request):
            assert request.url.path == "/data/symbol-token"
            assert request.headers["Authorization"] == self.access_token
            symbols = json.loads(request.content)["symbols"]
            return httpx.Response(
                200,
                json={"s": "ok", "validSymbol": {s: "10100000003045" for s in symbols}, "invalidSymbol": []},
            )

        self._http = httpx.Client(transport=httpx.MockTransport(handle))


def fyers_snapshot(topic_id, name, values, precision=2, multiplier=1):
    raw = name.encode()
    body = bytes([83]) + struct.pack("<H", topic_id) + bytes([len(raw)]) + raw + bytes([len(values)])
    body += (
        struct.pack(f">{len(values)}i", *values)
        + b"\x00\x00"
        + struct.pack(">H", multiplier)
        + bytes([precision])
    )
    for s in (b"NSE", b"3045", b"SBIN-EQ"):
        body += bytes([len(s)]) + s
    return body


def fyers_datafeed(records, message_number=1):
    return (
        struct.pack("!HB", 0, 6)
        + struct.pack(">I", message_number)
        + struct.pack("!H", len(records))
        + b"".join(records)
    )


def test_fyers_feed_auth_subscribe_snapshot_and_update():
    sf_values = [
        81250,
        150000,
        1790000000,
        1790000001,
        10,
        12,
        81245,
        81255,
        7,
        90000,
        80000,
        81230,
        0,
        80100,
        81500,
        90000,
        70000,
        70000,
        95000,
        80500,
        80900,
    ]
    dp_values = [81245 - 5 * k for k in range(5)] + [81255 + 5 * k for k in range(5)]
    dp_values += (
        [100 + k for k in range(5)]
        + [200 + k for k in range(5)]
        + [1 + k for k in range(5)]
        + [2 + k for k in range(5)]
    )

    def handler(ws, server):
        auth = ws.recv(timeout=5)
        server.received.append(auth)
        ws.recv(timeout=5)  # full mode
        # auth response: "K" and an ack count of 0
        ws.send(
            struct.pack("!HB", 0, 1)
            + b"\x00\x00"
            + struct.pack("!H", 1)
            + b"K"
            + b"\x02"
            + struct.pack("!H", 4)
            + struct.pack(">I", 0)
        )
        sub = ws.recv(timeout=5)
        server.received.append(sub)
        ws.send(
            fyers_datafeed(
                [
                    fyers_snapshot(7, "sf|nse_cm|3045", sf_values),
                    fyers_snapshot(8, "dp|nse_cm|3045", dp_values),
                ]
            )
        )
        update = bytes([85]) + struct.pack("<H", 7) + bytes([2]) + struct.pack(">2i", 81300, 150500)
        ws.send(fyers_datafeed([update], 2))
        time.sleep(1)

    server = Server(handler)
    hub = MarketDataHub(SystemClock())
    feed = FyersFeed(FyersStub(), hub, SystemClock(), connect=server.connect)
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: (b := hub.book(SBIN)) is not None and b.volume == 150_500)
        book = hub.book(SBIN)
        assert book.last_price == pytest.approx(813.00) and book.bids[0].price == pytest.approx(812.45)
        assert len(book.asks) == 5 and book.asks[0].quantity == 200 and book.bids[0].orders == 1
        assert book.total_buy_quantity == 90000 and book.last_quantity == 7
        auth = server.received[0]
        assert auth[2] == 1 and b"hsm-abc" in auth and b"PythonSDK" in auth
        sub = server.received[1]
        assert sub[2] == 4 and b"sf|nse_cm|3045" in sub and b"dp|nse_cm|3045" in sub
    finally:
        feed.stop()
        server.close()


# ---- Kotak Neo ------------------------------------------------------------------------------------------


class NeoStub:
    venue = "KOTAKNEO"
    _refs = {SBIN: "SBIN-EQ|3045"}

    def feed_credentials(self):
        return {"url": "wss://example.invalid/apifeed", "user": "UCC1", "auth": "sid-1"}


def neo_market_picture(token=3045, ltp=81250, volume=150000, level=16, depth=5):
    header = struct.pack("<HHbBBBB", 0, 7208, 1, level, 0, 1, 0)
    ltt = int(datetime(2026, 9, 28, 5, 32, 1, tzinfo=UTC).timestamp())
    body = struct.pack(
        "<IqqqqqIIIIIqIIIIhiIdiIIIIIBI",
        token,
        90000,
        80000,
        volume,
        ltt,
        ltt,
        80500,
        80900,
        81500,
        80100,
        ltp,
        7,
        81230,
        0,
        depth,
        depth,
        0,
        50,
        0,
        1.0e9,
        350,
        95000,
        70000,
        90000,
        70000,
        1,
        2,
        1,
    )
    rows = b"".join(struct.pack("<qii", 100 + k, 81245 - 5 * k, 1 + k) for k in range(depth))
    rows += b"".join(struct.pack("<qii", 200 + k, 81255 + 5 * k, 2 + k) for k in range(depth))
    packet = header + body + rows
    return struct.pack("<H", len(packet)) + packet[2:]


def test_neo_feed_auth_dividers_and_batched_depth():
    def handler(ws, server):
        server.received.append(json.loads(ws.recv(timeout=5)))
        ws.send(
            json.dumps(
                {
                    "message_code": 1119,
                    "format": "native_batch",
                    "exchanges": {"nse_cm": {"value": 1, "divider": 100}},
                }
            )
        )
        server.received.append(json.loads(ws.recv(timeout=5)))
        ws.send(json.dumps({"message_code": 1109, "trading_symbols": {"nse_cm|3045": "SBIN-EQ"}}))
        ws.send(neo_market_picture() + neo_market_picture(ltp=81300, volume=150500))
        time.sleep(1)

    server = Server(handler)
    hub = MarketDataHub(SystemClock())
    feed = NeoFeed(NeoStub(), hub, SystemClock(), connect=server.connect)
    feed.set_instruments([SBIN])
    feed.start()
    try:
        assert wait_for(lambda: (b := hub.book(SBIN)) is not None and b.volume == 150_500)
        book = hub.book(SBIN)
        assert book.last_price == pytest.approx(813.0) and book.bids[0].price == pytest.approx(812.45)
        assert book.asks[4].price == pytest.approx(812.75) and book.asks[0].orders == 2
        assert book.total_buy_quantity == 90000 and book.open_interest is None
        auth, sub = server.received
        assert auth["user"] == "UCC1" and auth["auth"] == "sid-1" and auth["format"] == "native_batch"
        assert sub == {"event": "subscribeFullDepth", "inputtoken": "nse_cm|3045", "ack_symbol": True}
    finally:
        feed.stop()
        server.close()


# ---- manager --------------------------------------------------------------------------------------------


def test_manager_streams_every_capable_broker_and_reports_coverage():
    def handler(ws, server):
        ws.recv(timeout=5)
        for k in range(30):
            ws.send(dhan_full_packet(volume=150_000 + k))
            time.sleep(0.05)

    server = Server(handler)

    class Connections:
        adapters = {"c-dhan": DhanStub()}

    hub = MarketDataHub(SystemClock())
    manager = FeedManager(Connections(), hub, SystemClock(), connect=server.connect)
    manager.sync([SBIN, "NSE:UNKNOWN-EQ"])
    try:
        assert wait_for(lambda: manager.covers(SBIN))
        assert not manager.covers("NSE:UNKNOWN-EQ")
        status = manager.status()
        assert {s["feed"] for s in status} == {"market", "depth20"}
        assert all(s["instruments"] == [SBIN] for s in status)
        manager.sync([])  # nothing watched: feeds stop
        assert manager.status() == []
    finally:
        manager.stop()
        server.close()
