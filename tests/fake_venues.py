"""In-memory venue servers speaking the Binance Spot and Alpaca REST protocols, for adapter tests."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import httpx


def _json(status: int, body) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


@dataclass
class FakeOrder:
    venue_id: str
    client_id: str
    symbol: str
    side: str
    type: str
    qty: Decimal
    price: Decimal | None
    status: str = "NEW"
    executed: Decimal = Decimal(0)
    trades: list[dict] = field(default_factory=list)


class FakeBinance:
    def __init__(
        self,
        *,
        key: str = "k",
        secret: str = "s",
        can_withdraw: bool = False,
        now_ms: int = 1_767_600_000_000,
    ):
        self.key, self.secret, self.can_withdraw = key, secret, can_withdraw
        self.now_ms = now_ms
        self.book = {"BTCUSDT": (Decimal("49999"), Decimal("50001"))}
        self.balances = {"USDT": Decimal(100_000), "BTC": Decimal(0)}
        self.orders: dict[str, FakeOrder] = {}
        self._ids = itertools.count(1000)
        self._trade_ids = itertools.count(1)
        self.fail_next: str | None = None
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport())

    # ---- test controls ------------------------------------------------------------------------

    def trade(self, symbol: str, price: str, qty: str) -> None:
        """Fill resting limit orders the given print crosses."""
        price_d, left = Decimal(price), Decimal(qty)
        for order in self.orders.values():
            if order.symbol != symbol or order.status not in ("NEW", "PARTIALLY_FILLED") or left <= 0:
                continue
            crosses = price_d <= order.price if order.side == "BUY" else price_d >= order.price
            if crosses:
                fill = min(left, order.qty - order.executed)
                self._fill(order, order.price, fill, maker=True)
                left -= fill

    def _fill(self, order: FakeOrder, price: Decimal, qty: Decimal, *, maker: bool) -> dict:
        order.executed += qty
        order.status = "FILLED" if order.executed == order.qty else "PARTIALLY_FILLED"
        trade = {
            "id": next(self._trade_ids),
            "orderId": int(order.venue_id),
            "price": str(price),
            "qty": str(qty),
            "commission": str(qty * price * Decimal("0.001")),
            "commissionAsset": "USDT",
            "time": self.now_ms,
            "isMaker": maker,
        }
        order.trades.append(trade)
        return trade

    # ---- protocol -----------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next == "timeout":
            self.fail_next = None
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if self.fail_next == "500":
            self.fail_next = None
            return _json(500, {"code": -1006, "msg": "Unexpected response"})
        url = urlsplit(str(request.url))
        path, raw = url.path, url.query
        params = dict(parse_qsl(raw))
        if "signature" in params:
            unsigned = raw.split("&signature=")[0]
            expected = hmac.new(self.secret.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
            if request.headers.get("X-MBX-APIKEY") != self.key:
                return _json(401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})
            if params["signature"] != expected:
                return _json(400, {"code": -1022, "msg": "Signature for this request is not valid."})
        route = (request.method, path)
        if route == ("GET", "/api/v3/time"):
            return _json(200, {"serverTime": self.now_ms})
        if route == ("GET", "/api/v3/exchangeInfo"):
            return _json(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                            "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                                {
                                    "filterType": "LOT_SIZE",
                                    "stepSize": "0.00001000",
                                    "minQty": "0.00001000",
                                    "maxQty": "9000",
                                },
                                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                            ],
                        }
                    ]
                },
            )
        if route == ("GET", "/api/v3/depth"):
            bid, ask = self.book[params["symbol"]]
            step = Decimal("0.01")
            return _json(
                200,
                {
                    "lastUpdateId": 1,
                    "bids": [[str(bid - k * step), str(1 + k)] for k in range(3)],
                    "asks": [[str(ask + k * step), str(2 + k)] for k in range(3)],
                },
            )
        if route == ("GET", "/api/v3/ticker/bookTicker"):
            bid, ask = self.book[params["symbol"]]
            return _json(
                200,
                {
                    "symbol": params["symbol"],
                    "bidPrice": str(bid),
                    "bidQty": "1",
                    "askPrice": str(ask),
                    "askQty": "1",
                },
            )
        if route == ("GET", "/api/v3/klines"):
            start = self.now_ms - 3 * 60_000
            rows = [
                [
                    start + i * 60_000,
                    "100",
                    "101",
                    "99",
                    str(100 + i),
                    "5",
                    start + (i + 1) * 60_000 - 1,
                    "500",
                    3,
                ]
                for i in range(3)
            ]
            return _json(200, rows)
        if route == ("GET", "/api/v3/account"):
            return _json(
                200,
                {
                    "canTrade": True,
                    "balances": [
                        {"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items()
                    ],
                },
            )
        if route == ("GET", "/sapi/v1/account/apiRestrictions"):
            return _json(200, {"enableWithdrawals": self.can_withdraw, "enableSpotAndMarginTrading": True})
        if route == ("POST", "/api/v3/order"):
            return self._new_order(params)
        if route == ("DELETE", "/api/v3/order"):
            order = self.orders.get(params["origClientOrderId"])
            if order is None or order.status not in ("NEW", "PARTIALLY_FILLED"):
                return _json(400, {"code": -2011, "msg": "Unknown order sent."})
            order.status = "CANCELED"
            return _json(200, {"status": "CANCELED", "clientOrderId": order.client_id})
        if route == ("GET", "/api/v3/order"):
            order = self.orders.get(params["origClientOrderId"])
            if order is None:
                return _json(400, {"code": -2013, "msg": "Order does not exist."})
            return _json(200, self._view(order))
        if route == ("GET", "/api/v3/myTrades"):
            order = next(o for o in self.orders.values() if o.venue_id == params["orderId"])
            return _json(200, order.trades)
        if route == ("POST", "/api/v3/order/cancelReplace"):
            old = self.orders.get(params["cancelOrigClientOrderId"])
            if old is None or old.status not in ("NEW", "PARTIALLY_FILLED"):
                return _json(
                    400,
                    {
                        "code": -2021,
                        "msg": "Order cancel-replace failed.",
                        "data": {"cancelResult": "FAILURE", "newOrderResult": "NOT_ATTEMPTED"},
                    },
                )
            old.status = "CANCELED"
            new = self._new_order(params, raw=True)
            return _json(
                200,
                {
                    "cancelResult": "SUCCESS",
                    "newOrderResult": "SUCCESS",
                    "cancelResponse": {"status": "CANCELED"},
                    "newOrderResponse": new,
                },
            )
        return _json(404, {"code": -1, "msg": f"no route {route}"})

    def _new_order(self, params: dict, raw: bool = False):
        qty = Decimal(params["quantity"])
        price = Decimal(params["price"]) if "price" in params else None
        bid, ask = self.book[params["symbol"]]
        if params["side"] == "BUY" and qty * (price or ask) > self.balances["USDT"]:
            return _json(
                400, {"code": -2010, "msg": "Account has insufficient balance for requested action."}
            )
        order = FakeOrder(
            str(next(self._ids)),
            params["newClientOrderId"],
            params["symbol"],
            params["side"],
            params["type"],
            qty,
            price,
        )
        crosses = price is not None and (price >= ask if order.side == "BUY" else price <= bid)
        if params["type"] == "LIMIT_MAKER" and crosses:
            return _json(400, {"code": -2010, "msg": "Order would immediately match and take."})
        self.orders[order.client_id] = order
        fills = []
        if params["type"] == "MARKET" or crosses:
            fills.append(self._fill(order, ask if order.side == "BUY" else bid, qty, maker=False))
        body = {
            **self._view(order),
            "transactTime": self.now_ms,
            "fills": [
                {
                    "price": f["price"],
                    "qty": f["qty"],
                    "commission": f["commission"],
                    "commissionAsset": f["commissionAsset"],
                    "tradeId": f["id"],
                }
                for f in fills
            ],
        }
        return body if raw else _json(200, body)

    @staticmethod
    def _view(order: FakeOrder) -> dict:
        return {
            "symbol": order.symbol,
            "orderId": int(order.venue_id),
            "clientOrderId": order.client_id,
            "status": order.status,
            "executedQty": str(order.executed),
            "origQty": str(order.qty),
        }


class FakeAlpaca:
    def __init__(self, *, key: str = "ak", secret: str = "as"):
        self.key, self.secret = key, secret
        self.quote = {"AAPL": (Decimal("199.99"), Decimal("200.01"))}
        self.cash = Decimal(50_000)
        self.orders: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.fail_next: str | None = None

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def fill(self, venue_id: str, qty: str, price: str) -> None:
        o = self.orders[venue_id]
        prev, add = Decimal(o["filled_qty"]), Decimal(qty)
        avg = Decimal(o["filled_avg_price"] or 0)
        total = prev + add
        o["filled_avg_price"] = str((avg * prev + Decimal(price) * add) / total)
        o["filled_qty"] = str(total)
        o["status"] = "filled" if total == Decimal(o["qty"]) else "partially_filled"
        o["filled_at"] = o["updated_at"] = "2026-01-05T15:00:00Z"

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next == "timeout":
            self.fail_next = None
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if (
            request.headers.get("APCA-API-KEY-ID") != self.key
            or request.headers.get("APCA-API-SECRET-KEY") != self.secret
        ):
            return _json(401, {"code": 40110000, "message": "request is not authorized"})
        url = urlsplit(str(request.url))
        path, params = url.path, dict(parse_qsl(url.query))
        body = json.loads(request.content) if request.content else {}
        m = request.method
        if (m, path) == ("GET", "/v2/account"):
            return _json(
                200, {"status": "ACTIVE", "trading_blocked": False, "cash": str(self.cash), "currency": "USD"}
            )
        if (m, path) == ("GET", "/v2/assets"):
            return _json(
                200, [{"symbol": "AAPL", "tradable": True, "fractionable": False, "status": "active"}]
            )
        if (m, path) == ("GET", "/v2/stocks/AAPL/quotes/latest"):
            bp, ap = self.quote["AAPL"]
            return _json(
                200,
                {
                    "symbol": "AAPL",
                    "quote": {"bp": str(bp), "bs": 1, "ap": str(ap), "as": 2, "t": "2026-01-05T15:00:00Z"},
                },
            )
        if (m, path) == ("GET", "/v2/stocks/AAPL/bars"):
            return _json(
                200, {"bars": [{"t": "2026-01-05T08:57:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]}
            )
        if (m, path) == ("POST", "/v2/orders"):
            if Decimal(body["qty"]) * Decimal(body.get("limit_price") or self.quote["AAPL"][1]) > self.cash:
                return _json(403, {"code": 40310000, "message": "insufficient buying power"})
            venue_id = f"a-{next(self._ids)}"
            order = {
                "id": venue_id,
                "client_order_id": body["client_order_id"],
                "status": "accepted",
                "qty": body["qty"],
                "limit_price": body.get("limit_price"),
                "filled_qty": "0",
                "filled_avg_price": None,
                "updated_at": "2026-01-05T15:00:00Z",
            }
            self.orders[venue_id] = order
            if body["type"] == "market":
                self.fill(venue_id, body["qty"], str(self.quote["AAPL"][1]))
            return _json(200, order)
        if (m, path) == ("GET", "/v2/orders:by_client_order_id"):
            for o in self.orders.values():
                if o["client_order_id"] == params["client_order_id"]:
                    return _json(200, o)
            return _json(404, {"code": 40410000, "message": "order not found"})
        if path.startswith("/v2/orders/"):
            venue_id = path.rsplit("/", 1)[1]
            order = self.orders.get(venue_id)
            if order is None:
                return _json(404, {"code": 40410000, "message": "order not found"})
            if m == "DELETE":
                if order["status"] in ("filled", "canceled"):
                    return _json(422, {"code": 42210000, "message": "order is not cancelable"})
                order["status"] = "canceled"
                return httpx.Response(204)
            if m == "PATCH":
                order["status"] = "replaced"
                new_id = f"a-{next(self._ids)}"
                new = {
                    **order,
                    "id": new_id,
                    "client_order_id": body["client_order_id"],
                    "status": "accepted",
                    "qty": body["qty"],
                    "limit_price": body["limit_price"],
                    "filled_qty": "0",
                    "filled_avg_price": None,
                }
                self.orders[new_id] = new
                return _json(200, new)
        return _json(404, {"code": 40410000, "message": f"no route {m} {path}"})


def utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def fake_jwt(exp: datetime) -> str:
    import base64

    def part(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': int(exp.timestamp())})}.sig"


SYMBOL_MASTER_CSV = (
    "10100000003045,STATE BANK OF INDIA,0,1,0.05,INE062A01020,0915-1530|1815-1915:,1691157600,,"
    "NSE:SBIN-EQ,10,10,3045,SBIN,3045,-1.0,XX,10100000003045,None,None,None\n"
    "10100000002885,RELIANCE INDUSTRIES LTD,0,1,0.1,INE002A01018,0915-1530|1815-1915:,1691157600,,"
    "NSE:RELIANCE-EQ,10,10,2885,RELIANCE,2885,-1.0,XX,10100000002885,None,None,None\n"
    "10100000014428,NIPPON INDIA ETF GOLD BEES,0,1,0.01,INF204KB17I5,0915-1530|1815-1915:,1691157600,,"
    "NSE:GOLDBEES-EQ,10,10,14428,GOLDBEES,14428,-1.0,XX,10100000014428,None,None,None\n"
    "101000000026000,NIFTY50-INDEX,10,1,0.05,,0915-1530|1815-1915:,1691157600,,"
    "NSE:NIFTY50-INDEX,10,10,26000,NIFTY50,26000,-1.0,XX,101000000026000,None,None,None\n"
)


def futures_master(now: datetime) -> dict:
    """MCX and NSE currency futures masters (JSON, keyed by ticker), front and next month plus noise."""
    near, far, too_far = (int((now + timedelta(days=d)).timestamp()) for d in (20, 50, 400))

    def fut(ticker, multiplier, tick, expiry, opt="XX"):
        return ticker, {"symTicker": ticker, "qtyMultiplier": multiplier, "tickSize": tick,
                        "expiryDate": str(expiry), "optType": opt}  # fmt: skip

    return {
        "MCX": dict([
            fut("MCX:GOLDM26JANFUT", 10, 1, near),
            fut("MCX:GOLDM26FEBFUT", 10, 1, far),
            fut("MCX:CRUDEOIL26JANFUT", 100, 1, near),
            fut("MCX:GOLDM27JANFUT", 10, 1, too_far),
            fut("MCX:GOLDM26JAN72000CE", 10, 1, near, "CE"),
        ]),
        "CDS": dict([fut("NSE:USDINR26JANFUT", 1000, 0.0025, near)]),
    }  # fmt: skip


class FakeFyers:
    """Fyers API v3 (trading + data + public symbol master) for NSE equities."""

    def __init__(self, *, app_id: str = "XA1234-100", secret: str = "fsecret", now: datetime | None = None):
        self.app_id, self.secret = app_id, secret
        self.now = now or datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        self.auth_code = "good-code"
        self.pin = "1234"
        self.token = fake_jwt(self.now.replace(hour=23))
        self.refresh_token = "refresh-1"
        self.quotes = {
            "NSE:SBIN-EQ": (Decimal("799.9"), Decimal("800.1")),
            "MCX:GOLDM26JANFUT": (Decimal("72000"), Decimal("72010")),
        }
        self.cash = Decimal(100_000)
        self.book: dict[str, dict] = {}
        self._ids = itertools.count(26010500001)
        self.fail_next: str | None = None
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def app_hash(self) -> str:
        return hashlib.sha256(f"{self.app_id}:{self.secret}".encode()).hexdigest()

    def fill(self, venue_id: str, qty: str, price: str) -> None:
        o = self.book[venue_id]
        q, p = Decimal(qty), Decimal(price)
        prev = Decimal(str(o["filledQty"]))
        avg = Decimal(str(o["tradedPrice"]))
        total = prev + q
        o["tradedPrice"] = float((avg * prev + p * q) / total)
        o["filledQty"] = int(total)
        o["remainingQuantity"] = int(Decimal(o["qty"]) - total)
        o["status"] = 2 if total >= o["qty"] else 6

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        path = url.path
        body = json.loads(request.content) if request.content else {}
        if url.netloc == "public.fyers.in":
            if path.endswith("MCX_COM_sym_master.json"):
                return _json(200, futures_master(self.now)["MCX"])
            if path.endswith("NSE_CD_sym_master.json"):
                return _json(200, futures_master(self.now)["CDS"])
            return httpx.Response(200, text=SYMBOL_MASTER_CSV)
        if path == "/api/v3/validate-authcode":
            if body.get("appIdHash") != self.app_hash() or body.get("code") != self.auth_code:
                return _json(400, {"s": "error", "code": -413, "message": "invalid auth code"})
            return _json(200, {"s": "ok", "access_token": self.token, "refresh_token": self.refresh_token})
        if path == "/api/v3/validate-refresh-token":
            if body.get("refresh_token") != self.refresh_token or body.get("pin") != self.pin:
                return _json(400, {"s": "error", "code": -501, "message": "invalid pin"})
            self.token = fake_jwt(self.now.replace(hour=23, minute=59))
            return _json(200, {"s": "ok", "access_token": self.token})
        if request.headers.get("Authorization") != f"{self.app_id}:{self.token}":
            return _json(401, {"s": "error", "code": -16, "message": "Could not authenticate the user"})
        if self.fail_next == "timeout":
            self.fail_next = None
            raise httpx.ReadTimeout("simulated timeout", request=request)
        params = dict(parse_qsl(url.query))
        match (request.method, path):
            case ("GET", "/api/v3/profile"):
                return _json(200, {"s": "ok", "data": {"name": "Test Trader", "fy_id": "XY01234"}})
            case ("GET", "/api/v3/funds"):
                return _json(200, {"s": "ok", "fund_limit": [
                    {"id": 1, "title": "Total Balance", "equityAmount": float(self.cash),
                     "commodityAmount": 0},
                    {"id": 10, "title": "Available Balance", "equityAmount": float(self.cash),
                     "commodityAmount": 0},
                ]})  # fmt: skip
            case ("GET", "/data/quotes"):
                d = []
                for sym in params["symbols"].split(","):
                    if sym in self.quotes:
                        bid, ask = self.quotes[sym]
                        v = {
                            "bid": float(bid),
                            "ask": float(ask),
                            "lp": float(ask),
                            "tt": int(self.now.timestamp()),
                            "open_price": float(bid) - 5,
                            "high_price": float(ask) + 3,
                            "low_price": float(bid) - 8,
                            "prev_close_price": float(bid) - 10,
                            "volume": 250_000,
                            "ch": 10.2,
                        }
                        d.append({"n": sym, "s": "ok", "v": v})
                return _json(200, {"s": "ok", "d": d})
            case ("GET", "/data/options-chain-v3"):
                assert params["symbol"] == "NSE:NIFTY50-INDEX"
                expiry = {"date": "08-01-2026", "expiry": "1767866400"}
                rows = [
                    {"option_type": "", "strike_price": -1, "ltp": 26_240.5, "symbol": "NSE:NIFTY50-INDEX"}
                ]
                for strike in range(26_000, 26_550, 50):
                    for kind in ("CE", "PE"):
                        itm = (26_240 - strike) if kind == "CE" else (strike - 26_240)
                        price = max(itm, 0) + 60.0
                        rows.append(
                            {
                                "option_type": kind,
                                "strike_price": strike,
                                "ltp": price,
                                "bid": price - 0.5,
                                "ask": price + 0.5,
                                "oi": 1_000_000 + (strike - 26_000) * (400 if kind == "CE" else -300),
                                "oich": 50_000,
                                "volume": 2_000_000,
                                "symbol": f"NSE:NIFTY26108{strike}{kind}",
                            }
                        )
                return _json(
                    200,
                    {
                        "s": "ok",
                        "code": 200,
                        "data": {
                            "callOi": 1,
                            "putOi": 1,
                            "expiryData": [expiry],
                            "optionsChain": rows if int(params.get("strikecount", 15)) > 1 else rows[:1],
                        },
                    },
                )
            case ("GET", "/data/depth"):
                sym = params["symbol"]
                bid, ask = self.quotes[sym]
                item = {
                    "totalbuyqty": 5000,
                    "totalsellqty": 4000,
                    "bids": [
                        {"price": float(bid), "volume": 300, "ord": 4},
                        {"price": float(bid) - 0.05, "volume": 150, "ord": 2},
                    ],
                    "ask": [
                        {"price": float(ask), "volume": 200, "ord": 3},
                        {"price": float(ask) + 0.05, "volume": 100, "ord": 1},
                    ],
                    "ltp": float(ask),
                    "ltq": 10,
                    "ltt": int(self.now.timestamp()),
                    "v": 123456,
                    "oi": 0,
                }
                return _json(200, {"s": "ok", "d": {sym: item}, "message": ""})
            case ("GET", "/data/history"):
                step = 86400 if params["resolution"] == "D" else int(params["resolution"]) * 60
                start, end = int(params["range_from"]), int(params["range_to"])
                start -= start % step
                candles = [[t, 800, 805, 795, 801, 1000] for t in range(start, end, step)]
                return _json(200, {"s": "ok", "candles": candles})
            case ("POST", "/api/v3/orders/sync"):
                return self._place(body)
            case ("PATCH", "/api/v3/orders/sync"):
                o = self.book.get(body.get("id", ""))
                if o is None or o["status"] != 6:
                    return _json(400, {"s": "error", "code": -52, "message": "Order not pending"})
                o["qty"], o["limitPrice"] = body["qty"], body["limitPrice"]
                return _json(
                    200, {"s": "ok", "code": 1102, "message": "Successfully modified order", "id": o["id"]}
                )
            case ("DELETE", "/api/v3/orders/sync"):
                o = self.book.get(body.get("id", ""))
                if o is None or o["status"] != 6:
                    return _json(400, {"s": "error", "code": -52, "message": "Order not pending"})
                o["status"] = 1
                return _json(
                    200, {"s": "ok", "code": 1103, "message": "Successfully cancelled order", "id": o["id"]}
                )
            case ("GET", "/api/v3/orders"):
                return _json(200, {"s": "ok", "orderBook": list(self.book.values())})
        return _json(404, {"s": "error", "code": -1, "message": f"no route {request.method} {path}"})

    def _place(self, body: dict) -> httpx.Response:
        bid, ask = self.quotes.get(body["symbol"], (None, None))
        if bid is None:
            return _json(400, {"s": "error", "code": -50, "message": "Invalid symbol"})
        price = Decimal(str(body["limitPrice"])) if body["type"] == 1 else ask
        if body["side"] == 1 and price * body["qty"] > self.cash:
            return _json(400, {"s": "error", "code": -99, "message": "RMS: Insufficient funds"})
        venue_id = str(next(self._ids))
        self.book[venue_id] = {
            "id": venue_id, "symbol": body["symbol"], "qty": body["qty"], "side": body["side"],
            "type": body["type"], "limitPrice": body["limitPrice"], "productType": body["productType"],
            "filledQty": 0, "remainingQuantity": body["qty"], "tradedPrice": 0, "status": 6,
            "orderTag": body.get("orderTag", ""), "orderDateTime": "05-Jan-2026 14:30:00", "message": "",
        }  # fmt: skip
        if body["type"] == 2:
            self.fill(venue_id, str(body["qty"]), str(ask if body["side"] == 1 else bid))
        return _json(
            200, {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": venue_id}
        )


# ---- OANDA v20 ---------------------------------------------------------------------------------


def _unix(at: datetime) -> str:
    return f"{at.timestamp():.9f}"


class FakeOanda:
    """OANDA's v20 REST API (practice host), as described by the official v20-python SDK."""

    HOST = "api-fxpractice.oanda.com"

    def __init__(self, *, token: str = "otoken", account: str = "101-001-1234567-001"):
        self.token, self.account = token, account
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # a Monday, markets open
        self.prices = {
            "XAU_USD": (Decimal("3999.80"), Decimal("4000.20")),
            "EUR_USD": (Decimal("1.16993"), Decimal("1.17007")),
            "USD_JPY": (Decimal("146.993"), Decimal("147.007")),
        }
        self.orders: dict[str, dict] = {}  # by order id
        self.transactions: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.fill_market = True  # False: FOK market orders are cancelled
        self._ids = itertools.count(100)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def _tx(self, **fields) -> dict:
        tx = {"id": str(next(self._ids)), "time": _unix(self.now), "accountID": self.account, **fields}
        self.transactions[tx["id"]] = tx
        return tx

    def _fill(self, order: dict, price: Decimal) -> dict:
        order["state"] = "FILLED"
        fill = self._tx(
            type="ORDER_FILL",
            orderID=order["id"],
            clientOrderID=order["clientExtensions"]["id"],
            instrument=order["instrument"],
            units=order["units"],
            price=str(price),
            commission="0.0000",
            financing="0.0000",
            halfSpreadCost="0.2000",
        )
        order["fillingTransactionID"] = fill["id"]
        return fill

    def fill_limit(self, client_id: str) -> None:
        order = self._by_client(client_id)
        self._fill(order, Decimal(order["price"]))

    def _by_client(self, client_id: str) -> dict | None:
        return next((o for o in self.orders.values() if o["clientExtensions"]["id"] == client_id), None)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        if url.hostname != self.HOST:
            return _json(404, {"errorMessage": "unknown host"})
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return _json(401, {"errorMessage": "Insufficient authorization to perform request."})
        assert request.headers.get("Accept-Datetime-Format") == "UNIX"
        params = dict(parse_qsl(url.query))
        path, acct = url.path, f"/v3/accounts/{self.account}"
        if path.startswith("/v3/accounts/") and not path.startswith(acct):
            return _json(403, {"errorMessage": "The provided request was forbidden."})
        if path == f"{acct}/summary":
            return _json(
                200,
                {
                    "account": {
                        "id": self.account,
                        "currency": "USD",
                        "balance": "100000.0000",
                        "NAV": "100000.0000",
                        "marginAvailable": "98000.0000",
                        "marginUsed": "2000.0000",
                    },
                    "lastTransactionID": "99",
                },
            )
        if path == f"{acct}/instruments":
            return _json(
                200,
                {
                    "instruments": [
                        {"name": "XAU_USD", "type": "METAL", "displayName": "Gold", "pipLocation": -2,
                         "displayPrecision": 2, "tradeUnitsPrecision": 0, "minimumTradeSize": "1",
                         "maximumOrderUnits": "5000", "marginRate": "0.05"},
                        {"name": "EUR_USD", "type": "CURRENCY", "displayName": "EUR/USD", "pipLocation": -4,
                         "displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1",
                         "maximumOrderUnits": "100000000", "marginRate": "0.0333"},
                        {"name": "USD_JPY", "type": "CURRENCY", "displayName": "USD/JPY", "pipLocation": -2,
                         "displayPrecision": 3, "tradeUnitsPrecision": 0, "minimumTradeSize": "1",
                         "maximumOrderUnits": "100000000", "marginRate": "0.0333"},
                    ]
                },
            )  # fmt: skip
        if path == f"{acct}/pricing":
            wanted = params.get("instruments", "").split(",")
            return _json(
                200,
                {
                    "prices": [
                        {
                            "type": "PRICE",
                            "instrument": name,
                            "time": _unix(self.now),
                            "tradeable": True,
                            "bids": [{"price": str(self.prices[name][0]), "liquidity": 1000000}],
                            "asks": [{"price": str(self.prices[name][1]), "liquidity": 1000000}],
                        }
                        for name in wanted
                        if name in self.prices
                    ]
                },
            )
        if path.startswith("/v3/instruments/") and path.endswith("/candles"):
            name = path.split("/")[3]
            step = {"M1": 60, "M15": 900, "H1": 3600, "D": 86400}[params["granularity"]]
            assert params["price"] == "M"
            count = int(params["count"])
            end = float(params["to"]) if "to" in params else self.now.timestamp()
            end -= end % step
            mid = float((self.prices[name][0] + self.prices[name][1]) / 2)
            rows = []
            for k in range(count, 0, -1):
                t = end - k * step
                p = mid * (1 + 0.0005 * ((int(t) // step) % 7 - 3))
                bar = {"o": f"{p:.5f}", "h": f"{p * 1.001:.5f}", "l": f"{p * 0.999:.5f}", "c": f"{p:.5f}"}
                rows.append({"time": f"{t:.9f}", "complete": True, "volume": 42, "mid": bar})
            rows.append(
                {"time": f"{end:.9f}", "complete": False, "volume": 3,
                 "mid": {"o": "1", "h": "1", "l": "1", "c": "1"}}
            )  # fmt: skip
            return _json(200, {"instrument": name, "granularity": params["granularity"], "candles": rows})
        if path == f"{acct}/orders" and request.method == "POST":
            return self._place(json.loads(request.content)["order"])
        if path.startswith(f"{acct}/orders/@"):
            rest = path[len(f"{acct}/orders/@") :]
            client_id, _, action = rest.partition("/")
            order = self._by_client(client_id)
            if order is None:
                return _json(404, {"errorCode": "ORDER_DOESNT_EXIST", "errorMessage": "Order not found"})
            if action == "cancel" and request.method == "PUT":
                if order["state"] != "PENDING":
                    return _json(404, {"errorCode": "ORDER_DOESNT_EXIST", "errorMessage": "not pending"})
                order["state"] = "CANCELLED"
                cancel = self._tx(type="ORDER_CANCEL", orderID=order["id"], reason="CLIENT_REQUEST")
                return _json(200, {"orderCancelTransaction": cancel, "lastTransactionID": cancel["id"]})
            return _json(200, {"order": order, "lastTransactionID": "999"})
        if path.startswith(f"{acct}/transactions/"):
            tx = self.transactions.get(path.rsplit("/", 1)[1])
            if tx is None:
                return _json(404, {"errorMessage": "Transaction not found"})
            return _json(200, {"transaction": tx, "lastTransactionID": "999"})
        return _json(404, {"errorMessage": f"no route {request.method} {path}"})

    def _place(self, body: dict) -> httpx.Response:
        name = body["instrument"]
        if name not in self.prices:
            reject = self._tx(type="MARKET_ORDER_REJECT", rejectReason="INSTRUMENT_UNKNOWN")
            return _json(400, {"orderRejectTransaction": reject, "errorCode": "INSTRUMENT_UNKNOWN",
                               "errorMessage": "unknown"})  # fmt: skip
        created = self._tx(type=f"{body['type']}_ORDER", **{k: v for k, v in body.items() if k != "type"})
        order = {**body, "id": created["id"], "state": "PENDING", "createTime": created["time"]}
        self.orders[order["id"]] = order
        out = {"orderCreateTransaction": created}
        bid, ask = self.prices[name]
        buying = Decimal(body["units"]) > 0
        if body["type"] == "MARKET":
            assert body["timeInForce"] == "FOK"
            if self.fill_market:
                out["orderFillTransaction"] = self._fill(order, ask if buying else bid)
            else:
                order["state"] = "CANCELLED"
                out["orderCancelTransaction"] = self._tx(
                    type="ORDER_CANCEL", orderID=order["id"], reason="MARKET_HALTED"
                )
        elif body["type"] == "LIMIT":
            limit = Decimal(body["price"])
            if (buying and ask <= limit) or (not buying and bid >= limit):
                out["orderFillTransaction"] = self._fill(order, ask if buying else bid)
        return _json(201, {**out, "lastTransactionID": str(next(self._ids))})
