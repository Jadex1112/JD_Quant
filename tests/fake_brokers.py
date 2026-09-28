# ruff: noqa: E501  (fixture data mirrors the brokers' long payloads)
"""In-memory servers for Zerodha Kite, Upstox, Angel One, Dhan and Delta Exchange India.

Each speaks the request and response shapes of the broker's official SDK closely enough to exercise the
adapters: authentication, instrument masters, quotes, candles, orders, the order book and balances.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import itertools
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import httpx

from jdquant.markets.india import IST
from jdquant.security.totp import code_at

BID, ASK = Decimal("800.00"), Decimal("800.50")


def _json(status: int, body) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


class _Book:
    """Orders shared by the Indian broker fakes: market orders fill at once, limits rest."""

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self._ids = itertools.count(1000)

    def place(self, *, symbol: str, side: str, qty: int, market: bool, price: float, tag: str) -> str:
        oid = str(next(self._ids))
        filled = market
        self.orders[oid] = {
            "id": oid, "symbol": symbol, "side": side, "qty": qty, "tag": tag,
            "status": "FILLED" if filled else "OPEN",
            "filled": qty if filled else 0,
            "avg": float(ASK if side == "BUY" else BID) if filled else 0.0,
            "price": price,
        }  # fmt: skip
        return oid

    def fill(self, oid: str) -> None:
        o = self.orders[oid]
        o.update(status="FILLED", filled=o["qty"], avg=o["price"])

    def cancel(self, oid: str) -> bool:
        o = self.orders.get(oid)
        if o is None or o["status"] != "OPEN":
            return False
        o["status"] = "CANCELLED"
        return True


def _candles(now: datetime, step: int, count: int, start_price: float = 790.0):
    end = int(now.timestamp()) - int(now.timestamp()) % step
    rows = []
    for k in range(count, -1, -1):  # includes the still-forming bar, which adapters must drop
        t = end - k * step
        p = start_price + (k % 9)
        rows.append((datetime.fromtimestamp(t, UTC), p, p + 2, p - 2, p + 1, 1000))
    return rows


# ---- Zerodha Kite --------------------------------------------------------------------------------------


class FakeKite(_Book):
    key, secret, request_token, token = "kitekey", "kitesecret", "reqtok", "kiteaccess"

    def __init__(self, now: datetime):
        super().__init__()
        self.now = now

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        path, query = url.path, dict(parse_qsl(url.query))
        form = dict(parse_qsl(request.content.decode())) if request.content else {}
        assert request.headers.get("X-Kite-Version") == "3"
        if path == "/session/token":
            checksum = hashlib.sha256(
                f"{self.key}{form.get('request_token')}{self.secret}".encode()
            ).hexdigest()
            if form.get("request_token") != self.request_token or form.get("checksum") != checksum:
                return _json(
                    403, {"status": "error", "error_type": "TokenException", "message": "Invalid checksum"}
                )
            return _json(
                200,
                {
                    "status": "success",
                    "data": {
                        "access_token": self.token,
                        "user_id": "AB1234",
                        "login_time": "2026-01-05 14:00:00",
                    },
                },
            )
        if request.headers.get("Authorization") != f"token {self.key}:{self.token}":
            return _json(
                403,
                {
                    "status": "error",
                    "error_type": "TokenException",
                    "message": "Incorrect api_key or access_token.",
                },
            )
        if path == "/user/profile":
            return _json(200, {"status": "success", "data": {"user_id": "AB1234"}})
        if path == "/user/margins":
            return _json(
                200,
                {
                    "status": "success",
                    "data": {
                        "equity": {
                            "net": 99000,
                            "available": {"live_balance": 100000, "cash": 100000},
                            "utilised": {"debits": 1000},
                        }
                    },
                },
            )
        if path == "/instruments/NSE":
            text = (
                "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
                "779521,3045,SBIN,STATE BANK OF INDIA,0,,0,0.05,1,EQ,NSE,NSE\n"
                "3693569,14428,GOLDBEES,NIPPON INDIA ETF GOLD BEES,0,,0,0.01,1,EQ,NSE,NSE\n"
                "256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n"
            )
            return httpx.Response(200, content=text.encode(), headers={"content-type": "text/csv"})
        if path == "/quote":
            key = query["i"]
            return _json(
                200,
                {
                    "status": "success",
                    "data": {
                        key: {
                            "timestamp": "2026-01-05 14:29:59",
                            "depth": {
                                "buy": [{"price": float(BID), "quantity": 10, "orders": 1}],
                                "sell": [{"price": float(ASK), "quantity": 12, "orders": 1}],
                            },
                        }
                    },
                },
            )
        if path.startswith("/instruments/historical/"):
            step = {"minute": 60, "5minute": 300, "15minute": 900, "60minute": 3600, "day": 86400}[
                path.split("/")[-1]
            ]
            rows = [
                [t.astimezone(IST).isoformat(), o, h, low, c, v]
                for t, o, h, low, c, v in _candles(self.now, step, 40)
            ]
            return _json(200, {"status": "success", "data": {"candles": rows}})
        if path == "/orders/regular" and request.method == "POST":
            oid = self.place(
                symbol=form["tradingsymbol"],
                side=form["transaction_type"],
                qty=int(form["quantity"]),
                market=form["order_type"] == "MARKET",
                price=float(form.get("price", 0)),
                tag=form.get("tag", ""),
            )
            return _json(200, {"status": "success", "data": {"order_id": oid}})
        if path.startswith("/orders/regular/") and request.method == "DELETE":
            oid = path.rsplit("/", 1)[1]
            if not self.cancel(oid):
                return _json(
                    400,
                    {
                        "status": "error",
                        "error_type": "OrderException",
                        "message": "Order cannot be cancelled",
                    },
                )
            return _json(200, {"status": "success", "data": {"order_id": oid}})
        if path == "/orders":
            status = {"FILLED": "COMPLETE", "OPEN": "OPEN", "CANCELLED": "CANCELLED"}
            return _json(
                200,
                {
                    "status": "success",
                    "data": [
                        {
                            "order_id": o["id"],
                            "status": status[o["status"]],
                            "filled_quantity": o["filled"],
                            "average_price": o["avg"],
                            "tag": o["tag"],
                            "status_message": None,
                            "exchange_timestamp": "2026-01-05 14:30:01",
                        }
                        for o in self.orders.values()
                    ],
                },
            )
        return _json(
            404, {"status": "error", "error_type": "GeneralException", "message": f"no route {path}"}
        )


# ---- Upstox ------------------------------------------------------------------------------------------------


class FakeUpstox(_Book):
    key, secret, code, token = "upkey", "upsecret", "upcode", "upaccess"

    def __init__(self, now: datetime):
        super().__init__()
        self.now = now
        self.redirects: list[str] = []

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        path, query = url.path, dict(parse_qsl(url.query))
        if url.hostname == "assets.upstox.com":
            rows = [
                {
                    "segment": "NSE_EQ",
                    "name": "STATE BANK OF INDIA",
                    "exchange": "NSE",
                    "instrument_type": "EQ",
                    "instrument_key": "NSE_EQ|INE062A01020",
                    "lot_size": 1,
                    "tick_size": 5.0,
                    "trading_symbol": "SBIN",
                },
                {
                    "segment": "NSE_EQ",
                    "name": "NIPPON INDIA ETF GOLD BEES",
                    "exchange": "NSE",
                    "instrument_type": "EQ",
                    "instrument_key": "NSE_EQ|INF204KB17I5",
                    "lot_size": 1,
                    "tick_size": 1.0,
                    "trading_symbol": "GOLDBEES",
                },
                {
                    "segment": "NSE_FO",
                    "instrument_type": "FUT",
                    "instrument_key": "NSE_FO|1",
                    "trading_symbol": "X",
                },
            ]
            return httpx.Response(200, content=gzip.compress(json.dumps(rows).encode()))
        if path == "/v2/login/authorization/token":
            form = dict(parse_qsl(request.content.decode()))
            self.redirects.append(form.get("redirect_uri", ""))
            if form != {
                "code": self.code,
                "client_id": self.key,
                "client_secret": self.secret,
                "redirect_uri": form.get("redirect_uri"),
                "grant_type": "authorization_code",
            } or not form.get("redirect_uri"):
                return _json(401, {"status": "error", "errors": [{"message": "Invalid Auth code"}]})
            return _json(200, {"access_token": self.token, "user_id": "UP1"})
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return _json(
                401, {"status": "error", "errors": [{"message": "Invalid token used to access API"}]}
            )
        if path == "/v2/user/profile":
            return _json(200, {"status": "success", "data": {"user_id": "UP1", "is_active": True}})
        if path == "/v2/user/get-funds-and-margin":
            return _json(
                200,
                {"status": "success", "data": {"equity": {"available_margin": 100000, "used_margin": 500}}},
            )
        if path == "/v2/market-quote/quotes":
            key = query["instrument_key"]
            return _json(
                200,
                {
                    "status": "success",
                    "data": {
                        "NSE_EQ:SBIN": {
                            "instrument_token": key,
                            "timestamp": "2026-01-05T14:29:59.000+05:30",
                            "depth": {
                                "buy": [{"price": float(BID), "quantity": 5, "orders": 1}],
                                "sell": [{"price": float(ASK), "quantity": 7, "orders": 1}],
                            },
                        }
                    },
                },
            )
        if path.startswith("/v3/historical-candle/"):
            parts = path.split("/")
            unit, size = parts[4], int(parts[5])
            step = {"minutes": 60, "hours": 3600, "days": 86400}[unit] * size
            rows = [
                [t.astimezone(IST).isoformat(), o, h, low, c, v, 0]
                for t, o, h, low, c, v in reversed(_candles(self.now, step, 40))
            ]
            return _json(200, {"status": "success", "data": {"candles": rows}})
        if path == "/v3/order/place":
            assert url.hostname == "api-hft.upstox.com"
            b = json.loads(request.content)
            oid = self.place(
                symbol=b["instrument_token"],
                side=b["transaction_type"],
                qty=b["quantity"],
                market=b["order_type"] == "MARKET",
                price=b["price"],
                tag=b["tag"],
            )
            return _json(200, {"status": "success", "data": {"order_ids": [oid]}})
        if path == "/v3/order/cancel":
            if not self.cancel(query["order_id"]):
                return _json(400, {"status": "error", "errors": [{"message": "Order not cancellable"}]})
            return _json(200, {"status": "success", "data": {"order_ids": [query["order_id"]]}})
        if path == "/v2/order/retrieve-all":
            status = {"FILLED": "complete", "OPEN": "open", "CANCELLED": "cancelled"}
            return _json(
                200,
                {
                    "status": "success",
                    "data": [
                        {
                            "order_id": o["id"],
                            "status": status[o["status"]],
                            "filled_quantity": o["filled"],
                            "average_price": o["avg"],
                            "tag": o["tag"],
                            "order_timestamp": "2026-01-05 14:30:01",
                        }
                        for o in self.orders.values()
                    ],
                },
            )
        return _json(404, {"status": "error", "errors": [{"message": f"no route {path}"}]})


# ---- Angel One --------------------------------------------------------------------------------------------


class FakeAngel(_Book):
    key, client_code, pin, totp_secret = "angelkey", "A12345", "1234", "JBSWY3DPEHPK3PXP"

    def __init__(self, now: datetime):
        super().__init__()
        self.now = now
        self.jwt = "jwt-1"
        self.logins = 0

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        path = url.path
        if url.hostname == "margincalculator.angelone.in":
            return _json(
                200,
                [
                    {
                        "token": "3045",
                        "symbol": "SBIN-EQ",
                        "name": "SBIN",
                        "expiry": "",
                        "strike": "-1.000000",
                        "lotsize": "1",
                        "instrumenttype": "",
                        "exch_seg": "NSE",
                        "tick_size": "5.000000",
                    },
                    {
                        "token": "14428",
                        "symbol": "GOLDBEES-EQ",
                        "name": "GOLDBEES",
                        "expiry": "",
                        "strike": "-1.000000",
                        "lotsize": "1",
                        "instrumenttype": "",
                        "exch_seg": "NSE",
                        "tick_size": "1.000000",
                    },
                    {
                        "token": "444",
                        "symbol": "GOLDM05FEB26FUT",
                        "name": "GOLDM",
                        "expiry": "05FEB2026",
                        "strike": "-1",
                        "lotsize": "100",
                        "instrumenttype": "FUTCOM",
                        "exch_seg": "MCX",
                        "tick_size": "100.000000",
                    },
                ],
            )
        assert request.headers.get("X-PrivateKey") == self.key and request.headers.get("X-UserType") == "USER"
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/loginByPassword"):
            expected = code_at(self.totp_secret, self.now)
            if body != {"clientcode": self.client_code, "password": self.pin, "totp": expected}:
                return _json(
                    200, {"status": False, "message": "Invalid totp", "errorcode": "AB1050", "data": None}
                )
            self.logins += 1
            return _json(
                200,
                {
                    "status": True,
                    "message": "SUCCESS",
                    "data": {"jwtToken": self.jwt, "refreshToken": "r", "feedToken": "f"},
                },
            )
        if request.headers.get("Authorization") != f"Bearer {self.jwt}":
            return _json(
                200, {"status": False, "message": "Invalid Token", "errorcode": "AG8001", "data": None}
            )
        ok = lambda data: _json(200, {"status": True, "message": "SUCCESS", "errorcode": "", "data": data})  # noqa: E731
        if path.endswith("/getRMS"):
            return ok({"net": "99000", "availablecash": "100000", "utiliseddebits": "1000"})
        if path.endswith("/market/v1/quote"):
            assert body["mode"] == "FULL"
            token = body["exchangeTokens"]["NSE"][0]
            return ok(
                {
                    "fetched": [
                        {
                            "exchange": "NSE",
                            "symbolToken": token,
                            "exchFeedTime": "05-Jan-2026 14:29:59",
                            "depth": {
                                "buy": [{"price": float(BID), "quantity": 3}],
                                "sell": [{"price": float(ASK), "quantity": 4}],
                            },
                        }
                    ]
                }
            )
        if path.endswith("/getCandleData"):
            step = {
                "ONE_MINUTE": 60,
                "FIVE_MINUTE": 300,
                "FIFTEEN_MINUTE": 900,
                "ONE_HOUR": 3600,
                "ONE_DAY": 86400,
            }[body["interval"]]
            return ok(
                [
                    [t.astimezone(IST).isoformat(), o, h, low, c, v]
                    for t, o, h, low, c, v in _candles(self.now, step, 40)
                ]
            )
        if path.endswith("/placeOrder"):
            oid = self.place(
                symbol=body["tradingsymbol"],
                side=body["transactiontype"],
                qty=int(body["quantity"]),
                market=body["ordertype"] == "MARKET",
                price=float(body["price"]),
                tag=body.get("ordertag", ""),
            )
            return ok({"orderid": oid, "uniqueorderid": f"u-{oid}"})
        if path.endswith("/cancelOrder"):
            if not self.cancel(body["orderid"]):
                return _json(
                    200, {"status": False, "message": "Order not found", "errorcode": "AB2001", "data": None}
                )
            return ok({"orderid": body["orderid"]})
        if path.endswith("/getOrderBook"):
            status = {"FILLED": "complete", "OPEN": "open", "CANCELLED": "cancelled"}
            rows = [
                {
                    "orderid": o["id"],
                    "status": status[o["status"]],
                    "filledshares": str(o["filled"]),
                    "averageprice": o["avg"],
                    "ordertag": o["tag"],
                    "text": "",
                    "updatetime": "05-Jan-2026 14:30:01",
                }
                for o in self.orders.values()
            ]
            return ok(rows or None)
        return _json(404, {"status": False, "message": f"no route {path}"})


# ---- Dhan -----------------------------------------------------------------------------------------------


def dhan_token(expires: datetime) -> str:
    import base64

    def part(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS512'})}.{part({'exp': int(expires.timestamp()), 'dhanClientId': '1100'})}.sig"


class FakeDhan(_Book):
    client_id = "1100"

    def __init__(self, now: datetime):
        super().__init__()
        self.now = now
        self.token = dhan_token(now + timedelta(hours=20))

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        path = url.path.removeprefix("/v2")
        if url.hostname == "images.dhan.co":
            text = (
                "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,"
                "SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
                "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME\n"
                "NSE,E,3045,EQUITY,0,SBIN,1.0,State Bank of India,,0,,5.0000,NA,ES,EQ,STATE BANK OF INDIA\n"
                "NSE,E,14428,EQUITY,0,GOLDBEES,1.0,Nippon Gold ETF,,0,,1.0000,NA,ETF,EQ,NIPPON INDIA ETF GOLD BEES\n"
                "MCX,M,444,FUTCOM,0,GOLDM-Feb2026-FUT,100.0,Gold Mini Feb,2026-02-05 23:30:00,0,,100.0000,M,FUT,NA,GOLD MINI\n"
            )
            return httpx.Response(200, content=text.encode(), headers={"content-type": "text/csv"})
        if (
            request.headers.get("access-token") != self.token
            or request.headers.get("client-id") != self.client_id
        ):
            return _json(
                401,
                {
                    "errorType": "Invalid_Authentication",
                    "errorCode": "DH-901",
                    "errorMessage": "Client ID or user generated access token is invalid or expired.",
                },
            )
        body = json.loads(request.content) if request.content else {}
        if body:
            assert body.pop("dhanClientId") == self.client_id
        if path == "/fundlimit":
            return _json(
                200, {"dhanClientId": self.client_id, "availabelBalance": 100000.0, "utilizedAmount": 1000.0}
            )
        if path == "/marketfeed/quote":
            sid = str(body["NSE_EQ"][0])
            return _json(
                200,
                {
                    "status": "success",
                    "data": {
                        "NSE_EQ": {
                            sid: {
                                "last_price": 800.2,
                                "depth": {
                                    "buy": [{"price": float(BID), "quantity": 9, "orders": 1}],
                                    "sell": [{"price": float(ASK), "quantity": 8, "orders": 1}],
                                },
                            }
                        }
                    },
                },
            )
        if path in ("/charts/intraday", "/charts/historical"):
            step = 86400 if path.endswith("historical") else int(body["interval"]) * 60
            rows = _candles(self.now, step, 40)
            return _json(
                200,
                {
                    "open": [r[1] for r in rows],
                    "high": [r[2] for r in rows],
                    "low": [r[3] for r in rows],
                    "close": [r[4] for r in rows],
                    "volume": [r[5] for r in rows],
                    "timestamp": [int(r[0].timestamp()) for r in rows],
                },
            )
        if path == "/orders" and request.method == "POST":
            oid = self.place(
                symbol=body["securityId"],
                side=body["transactionType"],
                qty=body["quantity"],
                market=body["orderType"] == "MARKET",
                price=body["price"],
                tag=body.get("correlationId", ""),
            )
            return _json(200, {"orderId": oid, "orderStatus": "TRANSIT"})
        if path.startswith("/orders/") and request.method == "DELETE":
            oid = path.rsplit("/", 1)[1]
            if not self.cancel(oid):
                return _json(
                    400,
                    {
                        "errorType": "Order_Error",
                        "errorCode": "DH-906",
                        "errorMessage": "Order not cancellable",
                    },
                )
            return _json(202, {"orderId": oid, "orderStatus": "CANCELLED"})
        if path == "/orders":
            status = {"FILLED": "TRADED", "OPEN": "PENDING", "CANCELLED": "CANCELLED"}
            return _json(
                200,
                [
                    {
                        "orderId": o["id"],
                        "orderStatus": status[o["status"]],
                        "filledQty": o["filled"],
                        "averageTradedPrice": o["avg"],
                        "correlationId": o["tag"],
                        "updateTime": "2026-01-05 14:30:01",
                    }
                    for o in self.orders.values()
                ],
            )
        return _json(404, {"errorMessage": f"no route {path}"})


# ---- Delta Exchange India -----------------------------------------------------------------------------------


class FakeDelta:
    key, secret = "dkey", "dsecret"
    HOST = "cdn-ind.testnet.deltaex.org"

    def __init__(self, now: datetime):
        self.now = now
        self.orders: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.bid, self.ask = Decimal("60000.0"), Decimal("60000.5")
        self._ids = itertools.count(500)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def _signed(self, request: httpx.Request) -> bool:
        url = urlsplit(str(request.url))
        query = f"?{url.query}" if url.query else ""
        message = (
            request.method
            + request.headers.get("timestamp", "")
            + url.path
            + query
            + request.content.decode()
        )
        expected = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return request.headers.get("api-key") == self.key and hmac.compare_digest(
            expected, request.headers.get("signature", "")
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        assert url.hostname == self.HOST
        path, query = url.path, dict(parse_qsl(url.query))
        ok = lambda result: _json(200, {"success": True, "result": result})  # noqa: E731
        if path == "/v2/products":
            return ok(
                [
                    {
                        "id": 84,
                        "symbol": "BTCUSD",
                        "contract_type": "perpetual_futures",
                        "state": "live",
                        "tick_size": "0.5",
                        "contract_value": "0.001",
                        "underlying_asset": {"symbol": "BTC"},
                        "quoting_asset": {"symbol": "USD"},
                    },
                    {
                        "id": 1699,
                        "symbol": "ETHUSD",
                        "contract_type": "perpetual_futures",
                        "state": "live",
                        "tick_size": "0.05",
                        "contract_value": "0.01",
                        "underlying_asset": {"symbol": "ETH"},
                        "quoting_asset": {"symbol": "USD"},
                    },
                    {
                        "id": 9,
                        "symbol": "C-BTC-70000-280926",
                        "contract_type": "call_options",
                        "state": "live",
                        "tick_size": "0.1",
                    },
                ]
            )
        if path.startswith("/v2/tickers/"):
            return ok(
                {
                    "symbol": path.rsplit("/", 1)[1],
                    "timestamp": int(self.now.timestamp() * 1_000_000),
                    "quotes": {
                        "best_bid": str(self.bid),
                        "best_ask": str(self.ask),
                        "bid_size": "120",
                        "ask_size": "80",
                    },
                }
            )
        if path == "/v2/history/candles":
            step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}[query["resolution"]]
            start, end = int(query["start"]), int(query["end"])
            rows = [
                {"time": t, "open": 60000, "high": 60100, "low": 59900, "close": 60050, "volume": 10}
                for t in range(start - start % step, end + 1, step)
            ]
            return ok(list(reversed(rows)))
        if not self._signed(request):
            return _json(401, {"success": False, "error": {"code": "Signature Mismatch"}})
        if path == "/v2/wallet/balances":
            return ok(
                [
                    {"asset_symbol": "USD", "balance": "1000", "available_balance": "900"},
                    {"asset_symbol": "BTC", "balance": "0", "available_balance": "0"},
                ]
            )
        if path == "/v2/orders" and request.method == "POST":
            b = json.loads(request.content)
            assert len(b["client_order_id"]) <= 32
            oid = next(self._ids)
            market = b["order_type"] == "market_order"
            order = {
                "id": oid,
                "product_id": b["product_id"],
                "size": b["size"],
                "side": b["side"],
                "client_order_id": b["client_order_id"],
                "limit_price": b.get("limit_price"),
                "state": "closed" if market else "open",
                "unfilled_size": 0 if market else b["size"],
                "average_fill_price": str(self.ask if b["side"] == "buy" else self.bid) if market else None,
            }
            self.orders[b["client_order_id"]] = order
            return ok(order)
        if path.startswith("/v2/orders/client_order_id/"):
            order = self.orders.get(path.rsplit("/", 1)[1])
            if order is None:
                return _json(404, {"success": False, "error": {"code": "order_not_found"}})
            return ok(order)
        if path == "/v2/orders" and request.method == "DELETE":
            b = json.loads(request.content)
            order = next((o for o in self.orders.values() if o["id"] == b["id"]), None)
            if order is None or order["state"] != "open":
                return _json(400, {"success": False, "error": {"code": "open_order_not_found"}})
            order["state"] = "cancelled"
            return ok(order)
        return _json(404, {"success": False, "error": {"code": f"no route {path}"}})

    def fill(self, client_order_id: str) -> None:
        order = self.orders[client_order_id]
        order.update(state="closed", unfilled_size=0, average_fill_price=order["limit_price"])
