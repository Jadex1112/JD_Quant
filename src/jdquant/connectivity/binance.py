"""Binance Spot adapter (REST API v3) — Chapters 45 and 84."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from jdquant.connectivity.base import (
    Balance,
    ConnectionTest,
    Environment,
    TokenBucket,
    VenueAdapter,
    VenueError,
    VenueTimeout,
)
from jdquant.core.clock import Clock
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentStatus
from jdquant.marketdata.records import Candle, Quote
from jdquant.oms.orders import Order, OrderStatus, OrderType, ReportType, TimeInForce

BASE_URLS = {
    Environment.PRODUCTION: "https://api.binance.com",
    Environment.TESTNET: "https://testnet.binance.vision",
}
INTERVALS = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    28800: "8h",
    43200: "12h",
    86400: "1d",
    259200: "3d",
    604800: "1w",
}
# Binance error codes → normalized reasons (FR-22005)
ERRORS = {
    -1003: ("RATE_LIMITED", True),
    -1013: ("INVALID_QUANTITY", False),
    -1021: ("TIMESTAMP_OUT_OF_WINDOW", True),
    -1022: ("SIGNATURE_INVALID", False),
    -1100: ("INVALID_PARAMETER", False),
    -1111: ("PRICE_OUT_OF_BAND", False),
    -1121: ("UNKNOWN_INSTRUMENT", False),
    -2010: ("ORDER_REJECTED", False),
    -2011: ("ORDER_NOT_WORKING", False),
    -2013: ("ORDER_NOT_FOUND", False),
    -2014: ("API_KEY_INVALID", False),
    -2015: ("API_KEY_INVALID", False),
}
UNKNOWN_OUTCOME = {-1006, -1007}  # "execution status unknown" — must be resolved by query
TIF = {TimeInForce.GTC: "GTC", TimeInForce.IOC: "IOC", TimeInForce.FOK: "FOK"}
S = OrderStatus


def _d(value: str | int | float) -> Decimal:
    return Decimal(str(value)).normalize()


def _ms(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000, tz=UTC)


class BinanceSpotAdapter(VenueAdapter):
    venue = "BINANCE"
    supports_replace = True

    def __init__(
        self,
        clock: Clock,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        environment: Environment = Environment.PRODUCTION,
        http: httpx.Client | None = None,
        recv_window_ms: int = 5000,
    ):
        super().__init__(clock, http=http)
        self._key = api_key
        self._secret = api_secret
        self.environment = environment
        self._base = BASE_URLS[environment]
        self._recv_window = recv_window_ms
        self.clock_offset_ms = 0.0
        self._orders_limiter = TokenBucket(10, 1.0)  # venue: 10 orders / second
        self._weight_limiter = TokenBucket(1200, 60.0)  # venue: 1200 request weight / minute
        self._fill_offset: dict[str, Decimal] = {}
        self._symbols: dict[str, Instrument] = {}

    # ---- transport ----------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        weight: int = 1,
    ) -> Any:
        self.guard()
        self._weight_limiter.acquire(weight)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not (self._key and self._secret):
                raise VenueError("CREDENTIALS_REQUIRED", "this operation needs API credentials")
            params["timestamp"] = int(time.time() * 1000 + self.clock_offset_ms)
            params["recvWindow"] = self._recv_window
            query = urlencode(params)
            params["signature"] = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self._key
        try:
            response = self._http.request(method, self._base + path, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        if response.status_code in (418, 429):
            self.record_failure()
            raise VenueError("RATE_LIMITED", "venue rate limit exceeded", retryable=True, raw=response.text)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"venue returned {response.status_code}")
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            code = body.get("code") if isinstance(body, dict) else None
            if code in UNKNOWN_OUTCOME:
                raise VenueTimeout(body.get("msg", "unknown execution status"))
            reason, retryable = ERRORS.get(code, ("VENUE_ERROR", False))
            msg = body.get("msg", response.text) if isinstance(body, dict) else response.text
            if reason == "ORDER_REJECTED" and "immediately match" in msg:
                reason = "POST_ONLY_WOULD_TAKE"
            elif reason == "ORDER_REJECTED" and "insufficient balance" in msg.lower():
                reason = "INSUFFICIENT_BALANCE"
            raise VenueError(reason, msg, retryable=retryable, raw=body)
        self.record_success()
        return body

    def sync_time(self) -> float:
        before = time.time() * 1000
        server = self._request("GET", "/api/v3/time")["serverTime"]
        after = time.time() * 1000
        self.clock_offset_ms = server - (before + after) / 2
        return self.clock_offset_ms

    # ---- connection & reference data ----------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        offset = self.sync_time()
        if not (self._key and self._secret):
            return ConnectionTest(True, False, False, None, offset, "public market data only")
        account = self._request("GET", "/api/v3/account", signed=True, weight=20)
        can_withdraw: bool | None
        try:
            restrictions = self._request("GET", "/sapi/v1/account/apiRestrictions", signed=True)
            can_withdraw = bool(restrictions.get("enableWithdrawals"))
        except VenueError:
            can_withdraw = None  # not exposed (e.g. testnet)
        balances = [
            Balance(b["asset"], _d(b["free"]), _d(b["locked"]))
            for b in account.get("balances", [])
            if Decimal(b["free"]) or Decimal(b["locked"])
        ]
        return ConnectionTest(True, True, bool(account.get("canTrade")), can_withdraw, offset, "ok", balances)

    def fetch_instruments(self) -> list[Instrument]:
        info = self._request("GET", "/api/v3/exchangeInfo", weight=20)
        instruments = []
        for s in info["symbols"]:
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            price, lot = filters.get("PRICE_FILTER", {}), filters.get("LOT_SIZE", {})
            notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            if not price or not lot:
                continue
            instrument = Instrument(
                venue=self.venue,
                symbol=s["symbol"],
                asset_class=AssetClass.CRYPTO_SPOT,
                base_asset=s["baseAsset"],
                quote_asset=s["quoteAsset"],
                tick_size=_d(price["tickSize"]),
                lot_size=_d(lot["stepSize"]),
                min_quantity=_d(lot["minQty"]),
                max_quantity=_d(lot["maxQty"]) if lot.get("maxQty") else None,
                min_notional=_d(notional["minNotional"]) if notional.get("minNotional") else None,
                status=InstrumentStatus.ACTIVE if s.get("status") == "TRADING" else InstrumentStatus.HALTED,
                aliases=(("BINANCE", s["symbol"]),),
            )
            self._symbols[instrument.instrument_id] = instrument
            instruments.append(instrument)
        return instruments

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        t = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": instrument.symbol}, weight=2)
        return Quote(
            instrument.instrument_id,
            self._clock.now(),
            _d(t["bidPrice"]),
            _d(t["bidQty"]),
            _d(t["askPrice"]),
            _d(t["askQty"]),
        )

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        interval = INTERVALS.get(interval_seconds)
        if interval is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"Binance has no {interval_seconds}s interval")
        rows = self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": instrument.symbol, "interval": interval, "limit": min(limit, 1000)},
            weight=2,
        )
        now_ms = self._clock.now().timestamp() * 1000
        return [
            Candle(
                instrument.instrument_id,
                interval_seconds,
                _ms(r[0]),
                _ms(r[6] + 1),
                _d(r[1]),
                _d(r[2]),
                _d(r[3]),
                _d(r[4]),
                _d(r[5]),
                _d(r[7]),
                int(r[8]),
            )
            for r in rows
            if r[6] < now_ms  # closed candles only
        ]

    def fetch_balances(self) -> list[Balance]:
        return self.test_connection().balances

    # ---- trading ------------------------------------------------------------------------------

    def _order_params(self, order: Order, quantity: Decimal, price: Decimal | None, client_id: str) -> dict:
        params: dict[str, Any] = {
            "symbol": order.instrument_id.split(":", 1)[1],
            "side": order.side.value,
            "quantity": f"{quantity.normalize():f}",
            "newClientOrderId": client_id,
            "newOrderRespType": "FULL",
        }
        if order.order_type is OrderType.MARKET:
            params["type"] = "MARKET"
        elif order.order_type is OrderType.LIMIT and order.post_only:
            params.update(type="LIMIT_MAKER", price=f"{price.normalize():f}")
        else:
            tif = TIF.get(order.time_in_force)
            if tif is None:
                raise VenueError(
                    "ORDER_TYPE_UNSUPPORTED", f"time in force {order.time_in_force} is not supported"
                )
            if order.order_type is OrderType.LIMIT:
                params.update(type="LIMIT", timeInForce=tif, price=f"{price.normalize():f}")
            elif order.order_type is OrderType.STOP_LIMIT:
                params.update(
                    type="STOP_LOSS_LIMIT",
                    timeInForce=tif,
                    price=f"{price.normalize():f}",
                    stopPrice=f"{order.stop_price.normalize():f}",
                )
            else:
                params.update(type="STOP_LOSS", stopPrice=f"{order.stop_price.normalize():f}")
        return params

    def submit(self, order: Order) -> None:
        try:
            self._orders_limiter.acquire()
            params = self._order_params(order, order.quantity, order.limit_price, order.client_order_id)
            response = self._request("POST", "/api/v3/order", params, signed=True)
        except VenueTimeout:
            return  # outcome unknown: the OMS ack timeout resolves it by query (FR-21046)
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        self._apply_order_response(order, response)

    def _apply_order_response(self, order: Order, response: dict, client_id: str | None = None) -> None:
        kw = {"client_order_id": client_id} if client_id else {}
        self.emit(ReportType.ACK, order, venue_order_id=str(response["orderId"]), **kw)
        for f in response.get("fills", []):
            self.emit_fill(
                order,
                trade_id=str(f["tradeId"]),
                price=_d(f["price"]),
                quantity=_d(f["qty"]),
                fee=_d(f["commission"]),
                fee_asset=f["commissionAsset"],
                is_maker=False,
                at=_ms(response.get("transactTime", int(time.time() * 1000))),
            )
        self._apply_terminal(order, response["status"])

    def _apply_terminal(self, order: Order, status: str) -> None:
        if status in ("CANCELED", "PENDING_CANCEL"):
            self.emit(ReportType.CANCELED, order)
        elif status in ("EXPIRED", "EXPIRED_IN_MATCH"):
            self.emit(ReportType.EXPIRED, order, reason=status)
        elif status == "REJECTED":
            self.emit(ReportType.REJECT, order, reason="ORDER_REJECTED")

    def cancel(self, order: Order) -> None:
        try:
            self._request(
                "DELETE",
                "/api/v3/order",
                {"symbol": order.instrument_id.split(":", 1)[1], "origClientOrderId": order.client_order_id},
                signed=True,
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.CANCELED, order)

    def replace(self, order: Order, quantity: Decimal, limit_price: Decimal) -> None:
        """cancelReplace with STOP_ON_FAILURE: the new order gets a new client id."""
        new_client_id = f"JQR-{uuid.uuid4().hex[:30]}"
        remaining = quantity - order.filled_quantity
        params = self._order_params(order, remaining, limit_price, new_client_id)
        params.update(cancelReplaceMode="STOP_ON_FAILURE", cancelOrigClientOrderId=order.client_order_id)
        try:
            self._orders_limiter.acquire()
            response = self._request("POST", "/api/v3/order/cancelReplace", params, signed=True)
        except VenueTimeout:
            return
        except VenueError as exc:
            data = (exc.details or {}).get("raw", {}).get("data", {}) if isinstance(exc.details, dict) else {}
            if data.get("cancelResult") == "SUCCESS":
                self.emit(ReportType.CANCELED, order, reason="replace failed after cancel")
            else:
                self.emit(ReportType.REPLACE_REJECT, order, reason=exc.code)
            return
        new = response["newOrderResponse"]
        self._fill_offset[new_client_id] = order.filled_quantity
        self.emit(
            ReportType.REPLACED, order, new_client_order_id=new_client_id, venue_order_id=str(new["orderId"])
        )
        for f in new.get("fills", []):
            self.emit_fill(
                order,
                trade_id=str(f["tradeId"]),
                price=_d(f["price"]),
                quantity=_d(f["qty"]),
                fee=_d(f["commission"]),
                fee_asset=f["commissionAsset"],
                is_maker=False,
                at=self._clock.now(),
            )

    def query(self, order: Order) -> None:
        try:
            status = self._request(
                "GET",
                "/api/v3/order",
                {"symbol": order.instrument_id.split(":", 1)[1], "origClientOrderId": order.client_order_id},
                signed=True,
                weight=4,
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            if exc.code == "ORDER_NOT_FOUND":
                self.emit(ReportType.NOT_FOUND, order)
            return
        self._reconcile(order, status)

    def poll(self, orders: list[Order]) -> None:
        for order in orders:
            if order.status in (
                S.SUBMITTED,
                S.UNKNOWN,
                S.OPEN,
                S.PARTIALLY_FILLED,
                S.PENDING_CANCEL,
                S.PENDING_REPLACE,
            ):
                self.query(order)

    def _reconcile(self, order: Order, status: dict) -> None:
        if order.status in (S.SUBMITTED, S.UNKNOWN):
            self.emit(ReportType.ACK, order, venue_order_id=str(status["orderId"]))
        executed = _d(status["executedQty"]) + self._fill_offset.get(order.client_order_id, Decimal(0))
        if executed > order.filled_quantity:
            trades = self._request(
                "GET",
                "/api/v3/myTrades",
                {"symbol": status["symbol"], "orderId": status["orderId"]},
                signed=True,
                weight=20,
            )
            for t in trades:
                self.emit_fill(
                    order,
                    trade_id=str(t["id"]),
                    price=_d(t["price"]),
                    quantity=_d(t["qty"]),
                    fee=_d(t["commission"]),
                    fee_asset=t["commissionAsset"],
                    is_maker=t["isMaker"],
                    at=_ms(t["time"]),
                )
        self._apply_terminal(order, status["status"])
