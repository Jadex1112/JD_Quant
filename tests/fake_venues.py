"""In-memory venue servers speaking the Binance Spot and Alpaca REST protocols, for adapter tests."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
