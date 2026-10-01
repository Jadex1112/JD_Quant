"""Alpaca broker adapter (Trading API v2, Market Data v2) — Chapters 46 and 85."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

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

TRADING_URLS = {
    Environment.PRODUCTION: "https://api.alpaca.markets",
    Environment.TESTNET: "https://paper-api.alpaca.markets",
}
DATA_URL = "https://data.alpaca.markets"
TIMEFRAMES = {60: "1Min", 300: "5Min", 900: "15Min", 1800: "30Min", 3600: "1Hour", 86400: "1Day"}
TIF = {TimeInForce.GTC: "gtc", TimeInForce.DAY: "day", TimeInForce.IOC: "ioc", TimeInForce.FOK: "fok"}
TYPES = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP_MARKET: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
}
S = OrderStatus


def _d(value: Any) -> Decimal:
    return Decimal(str(value)).normalize()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class AlpacaAdapter(VenueAdapter):
    venue = "ALPACA"
    supports_replace = True

    def __init__(
        self,
        clock: Clock,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        environment: Environment = Environment.TESTNET,
        http: httpx.Client | None = None,
    ):
        super().__init__(clock, http=http)
        self._key = api_key
        self._secret = api_secret
        self.environment = environment
        self._base = TRADING_URLS[environment]
        self._limiter = TokenBucket(200, 60.0)  # venue: 200 requests / minute
        self._venue_ids: dict[str, str] = {}
        self._seen: dict[str, tuple[Decimal, Decimal]] = {}
        self._fill_offset: dict[str, Decimal] = {}

    def can_replace(self, order: Order) -> bool:
        """Amend in place only before any fill; otherwise the OMS cancels and re-submits."""
        return order.filled_quantity == 0

    # ---- transport ----------------------------------------------------------------------------

    def _request(self, method: str, url: str, *, params: dict | None = None, json: dict | None = None) -> Any:
        if not (self._key and self._secret):
            raise VenueError("CREDENTIALS_REQUIRED", "Alpaca requires API credentials")
        self.guard()
        self._limiter.acquire()
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret}
        try:
            response = self._http.request(method, url, params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        if response.status_code == 429:
            self.record_failure()
            raise VenueError("RATE_LIMITED", "venue rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"venue returned {response.status_code}")
        body = response.json() if response.content else {}
        if response.status_code == 404:
            raise VenueError(
                "ORDER_NOT_FOUND", body.get("message", "not found") if isinstance(body, dict) else ""
            )
        if response.status_code >= 400:
            message = body.get("message", response.text) if isinstance(body, dict) else response.text
            lowered = message.lower()
            if response.status_code in (401, 403) and "buying power" not in lowered:
                code = "API_KEY_INVALID"
            elif "buying power" in lowered or "insufficient" in lowered:
                code = "INSUFFICIENT_BALANCE"
            elif "qty" in lowered or "quantity" in lowered:
                code = "INVALID_QUANTITY"
            elif "price" in lowered:
                code = "PRICE_OUT_OF_BAND"
            elif "market" in lowered and "closed" in lowered:
                code = "MARKET_CLOSED"
            else:
                code = "ORDER_REJECTED"
            raise VenueError(code, message, raw=body)
        self.record_success()
        return body

    # ---- connection & reference data ----------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        account = self._request("GET", f"{self._base}/v2/account")
        cash = Balance(account.get("currency", "USD"), _d(account.get("cash", "0")), Decimal(0))
        can_trade = account.get("status") == "ACTIVE" and not account.get("trading_blocked", False)
        # Alpaca API keys cannot move funds, so withdrawal permission is structurally absent.
        return ConnectionTest(True, True, can_trade, False, None, account.get("status", ""), [cash])

    def fetch_instruments(self) -> list[Instrument]:
        assets = self._request(
            "GET", f"{self._base}/v2/assets", params={"status": "active", "asset_class": "us_equity"}
        )
        instruments = []
        for a in assets:
            if not a.get("tradable"):
                continue
            fractional = bool(a.get("fractionable"))
            lot = _d(a.get("min_trade_increment") or ("0.000000001" if fractional else "1"))
            instruments.append(
                Instrument(
                    venue=self.venue,
                    symbol=a["symbol"],
                    asset_class=AssetClass.EQUITY,
                    base_asset=a["symbol"],
                    quote_asset="USD",
                    tick_size=_d(a.get("price_increment") or "0.01"),
                    lot_size=lot,
                    min_quantity=_d(a.get("min_order_size") or lot),
                    min_notional=Decimal(1) if fractional else None,
                    status=InstrumentStatus.ACTIVE
                    if a.get("status") == "active"
                    else InstrumentStatus.HALTED,
                    aliases=(("ALPACA", a["symbol"]),),
                )
            )
        return instruments

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        body = self._request("GET", f"{DATA_URL}/v2/stocks/{instrument.symbol}/quotes/latest")
        q = body.get("quote") or {}
        if not q.get("bp") or not q.get("ap"):
            return None
        return Quote(
            instrument.instrument_id, _ts(q["t"]), _d(q["bp"]), _d(q["bs"]), _d(q["ap"]), _d(q["as"])
        )

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        timeframe = TIMEFRAMES.get(interval_seconds)
        if timeframe is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"Alpaca has no {interval_seconds}s timeframe")
        start = self._clock.now() - timedelta(seconds=interval_seconds * limit * 3)
        body = self._request(
            "GET",
            f"{DATA_URL}/v2/stocks/{instrument.symbol}/bars",
            params={"timeframe": timeframe, "limit": min(limit, 10_000), "start": start.isoformat()},
        )
        step = timedelta(seconds=interval_seconds)
        now = self._clock.now()
        candles = []
        for b in body.get("bars") or []:
            open_ts = _ts(b["t"])
            if open_ts + step <= now:
                candles.append(
                    Candle(
                        instrument.instrument_id,
                        interval_seconds,
                        open_ts,
                        open_ts + step,
                        _d(b["o"]),
                        _d(b["h"]),
                        _d(b["l"]),
                        _d(b["c"]),
                        _d(b["v"]),
                        trade_count=int(b.get("n", 0)),
                        vwap=_d(b["vw"]) if b.get("vw") else None,
                    )
                )
        return candles[-limit:]

    def fetch_positions(self):
        return {
            f"ALPACA:{p['symbol']}": _d(p.get("qty", 0))
            for p in self._request("GET", f"{self._base}/v2/positions") or []
        }

    def fetch_balances(self) -> list[Balance]:
        return self.test_connection().balances

    # ---- trading ------------------------------------------------------------------------------

    def _body(self, order: Order, quantity: Decimal, limit_price: Decimal | None, client_id: str) -> dict:
        tif = TIF.get(order.time_in_force)
        if tif is None or order.post_only:
            raise VenueError("ORDER_TYPE_UNSUPPORTED", "unsupported time in force or post-only flag")
        body: dict[str, Any] = {
            "symbol": order.instrument_id.split(":", 1)[1],
            "qty": f"{quantity.normalize():f}",
            "side": order.side.value.lower(),
            "type": TYPES[order.order_type],
            "time_in_force": tif,
            "client_order_id": client_id,
        }
        if limit_price is not None:
            body["limit_price"] = f"{limit_price.normalize():f}"
        if order.stop_price is not None:
            body["stop_price"] = f"{order.stop_price.normalize():f}"
        return body

    def submit(self, order: Order) -> None:
        try:
            body = self._body(order, order.quantity, order.limit_price, order.client_order_id)
            response = self._request("POST", f"{self._base}/v2/orders", json=body)
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        self._reconcile(order, response)

    def cancel(self, order: Order) -> None:
        venue_id = order.venue_order_id or self._venue_ids.get(order.client_order_id)
        if venue_id is None:
            self.emit(ReportType.CANCEL_REJECT, order, reason="ORDER_NOT_WORKING")
            return
        try:
            self._request("DELETE", f"{self._base}/v2/orders/{venue_id}")
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.query(order)  # Alpaca cancels asynchronously; report the resulting state

    def replace(self, order: Order, quantity: Decimal, limit_price: Decimal) -> None:
        venue_id = order.venue_order_id or self._venue_ids.get(order.client_order_id)
        new_client_id = f"JQR-{uuid.uuid4().hex[:30]}"
        try:
            response = self._request(
                "PATCH",
                f"{self._base}/v2/orders/{venue_id}",
                json={
                    "qty": f"{quantity.normalize():f}",
                    "limit_price": f"{limit_price.normalize():f}",
                    "client_order_id": new_client_id,
                },
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.REPLACE_REJECT, order, reason=exc.code)
            return
        self._fill_offset[new_client_id] = order.filled_quantity
        self._venue_ids[new_client_id] = response["id"]
        self.emit(
            ReportType.REPLACED, order, new_client_order_id=new_client_id, venue_order_id=response["id"]
        )

    def query(self, order: Order) -> None:
        try:
            response = self._request(
                "GET",
                f"{self._base}/v2/orders:by_client_order_id",
                params={"client_order_id": order.client_order_id},
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            if exc.code == "ORDER_NOT_FOUND":
                self.emit(ReportType.NOT_FOUND, order)
            return
        self._reconcile(order, response)

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

    def _reconcile(self, order: Order, venue: dict) -> None:
        client_id = order.client_order_id
        self._venue_ids[client_id] = venue["id"]
        status = venue["status"]
        if status == "rejected":
            self.emit(ReportType.REJECT, order, reason="ORDER_REJECTED")
            return
        if order.status in (S.SUBMITTED, S.UNKNOWN):
            self.emit(ReportType.ACK, order, venue_order_id=venue["id"])
        offset = self._fill_offset.get(client_id, Decimal(0))
        filled = _d(venue.get("filled_qty") or "0")
        avg = _d(venue["filled_avg_price"]) if venue.get("filled_avg_price") else Decimal(0)
        prev_filled, prev_avg = self._seen.get(client_id, (order.filled_quantity - offset, Decimal(0)))
        if prev_filled and not prev_avg and order.average_fill_price is not None:
            prev_avg = order.average_fill_price
        if filled > prev_filled:
            delta = filled - prev_filled
            price = (avg * filled - prev_avg * prev_filled) / delta
            self.emit_fill(
                order,
                trade_id=f"{venue['id']}:{filled}",
                price=price,
                quantity=delta,
                fee=Decimal(0),
                fee_asset="USD",
                is_maker=None,
                at=_ts(venue.get("filled_at") or venue["updated_at"]),
            )
        self._seen[client_id] = (filled, avg)
        if status in ("canceled", "done_for_day"):
            self.emit(ReportType.CANCELED, order)
        elif status == "expired":
            self.emit(ReportType.EXPIRED, order, reason="expired")
