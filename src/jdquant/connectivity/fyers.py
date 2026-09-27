"""Fyers broker adapter (API v3) for NSE cash equities.

Endpoints and payloads follow the official `fyers-apiv3` SDK: trading calls go to
https://api-t1.fyers.in/api/v3, market data to https://api-t1.fyers.in/data, and every call carries
`Authorization: <app id>:<access token>`. Login is Fyers' OAuth flow: the user signs in on fyers.in, which
redirects back with an auth code that is exchanged for a day-scoped access token and a 15-day refresh token.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
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
from jdquant.core.types import Side
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentStatus
from jdquant.marketdata.records import Candle, Quote
from jdquant.markets.india import IST, IndiaEquityFees, Product
from jdquant.oms.orders import Order, OrderStatus, OrderType, ReportType, TimeInForce

API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
SYMBOL_MASTER = "https://public.fyers.in/sym_details/NSE_CM.csv"

RESOLUTIONS = {
    60: "1", 120: "2", 180: "3", 300: "5", 600: "10", 900: "15", 1200: "20",
    1800: "30", 3600: "60", 7200: "120", 14400: "240", 86400: "D",
}  # fmt: skip
ORDER_TYPES = {OrderType.LIMIT: 1, OrderType.MARKET: 2, OrderType.STOP_MARKET: 3, OrderType.STOP_LIMIT: 4}
VALIDITY = {TimeInForce.DAY: "DAY", TimeInForce.GTC: "DAY", TimeInForce.IOC: "IOC"}
# Order book status codes.
CANCELLED, FILLED, TRANSIT, REJECTED, PENDING, EXPIRED = 1, 2, 4, 5, 6, 7
# Symbol master columns (NSE_CM.csv has no header row).
COL_NAME, COL_TYPE, COL_LOT, COL_TICK, COL_TICKER = 1, 2, 3, 4, 9
AUTH_ERROR_CODES = {-8, -15, -16, -17, -300}
S = OrderStatus


def _d(value: Any) -> Decimal:
    return Decimal(str(value)).normalize()


def _num(value: Decimal) -> float | int:
    """Fyers takes JSON numbers; quantities are whole shares."""
    return int(value) if value == value.to_integral_value() else float(value)


def token_expiry(token: str) -> datetime | None:
    """Read the `exp` claim of a Fyers access token (a JWT) without verifying it."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


class FyersAdapter(VenueAdapter):
    venue = "FYERS"
    markets = ("NSE",)
    supports_replace = True
    requires_login = True

    def __init__(
        self,
        clock: Clock,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        environment: Environment = Environment.PRODUCTION,
        http: httpx.Client | None = None,
        product: Product = Product.CNC,
    ):
        super().__init__(clock, http=http)
        if environment is not Environment.PRODUCTION:
            raise VenueError(
                "ENVIRONMENT_UNSUPPORTED",
                "Fyers has no test environment; use the paper account to trade without real money",
            )
        self.app_id = api_key or ""
        self._secret = api_secret or ""
        self.environment = environment
        self.product = product
        self.fees = IndiaEquityFees(product=product)
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.pin: str | None = None
        self.on_session_changed: Callable[[dict[str, str | None]], None] | None = None
        self._per_second = TokenBucket(10, 1.0)
        self._per_minute = TokenBucket(200, 60.0)
        self._venue_ids: dict[str, str] = {}
        self._seen: dict[str, tuple[Decimal, Decimal]] = {}  # venue order id -> (filled qty, avg price)
        self._book: dict[str, dict] = {}
        self._last_refresh_attempt: datetime | None = None

    # ---- login --------------------------------------------------------------------------------

    @property
    def app_id_hash(self) -> str:
        return hashlib.sha256(f"{self.app_id}:{self._secret}".encode()).hexdigest()

    def login_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {"client_id": self.app_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state}
        )
        return f"{API}/generate-authcode?{query}"

    def complete_login(self, auth_code: str) -> None:
        body = self._post_auth(
            "/validate-authcode",
            {"grant_type": "authorization_code", "appIdHash": self.app_id_hash, "code": auth_code},
        )
        self._set_tokens(body["access_token"], body.get("refresh_token") or self.refresh_token)

    def set_session(self, session: dict[str, str | None]) -> None:
        self.access_token = session.get("access_token")
        self.refresh_token = session.get("refresh_token")
        self.pin = session.get("pin")

    def session(self) -> dict[str, str | None]:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "pin": self.pin}

    def is_ready(self) -> bool:
        """A usable session exists, renewing an expired one (at most every few minutes) when possible."""
        if not self.access_token:
            return False
        expires = self.session_expires_at()
        if expires is None or expires > self._clock.now() + timedelta(minutes=1):
            return True
        now = self._clock.now()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < timedelta(minutes=5):
            return False
        self._last_refresh_attempt = now
        return self.refresh()

    def session_expires_at(self) -> datetime | None:
        return token_expiry(self.access_token) if self.access_token else None

    def refresh(self) -> bool:
        """Renew the access token with the refresh token and PIN; False when the user must sign in again."""
        if not (self.refresh_token and self.pin):
            return False
        try:
            body = self._post_auth(
                "/validate-refresh-token",
                {
                    "grant_type": "refresh_token",
                    "appIdHash": self.app_id_hash,
                    "refresh_token": self.refresh_token,
                    "pin": self.pin,
                },
            )
        except VenueError:
            return False
        self._set_tokens(body["access_token"], self.refresh_token)
        return True

    def _set_tokens(self, access: str, refresh: str | None) -> None:
        self.access_token, self.refresh_token = access, refresh
        if self.on_session_changed is not None:
            self.on_session_changed(self.session())

    def _post_auth(self, path: str, payload: dict) -> dict:
        try:
            response = self._http.post(f"{API}{path}", json=payload)
        except httpx.TimeoutException as exc:
            raise VenueError("VENUE_UNAVAILABLE", f"Fyers login timed out: {exc}", retryable=True) from exc
        except httpx.TransportError as exc:
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400 or body.get("s") != "ok" or not body.get("access_token"):
            raise VenueError("LOGIN_FAILED", body.get("message") or f"HTTP {response.status_code}", raw=body)
        return body

    def _ensure_session(self) -> None:
        if not self.app_id or not self._secret:
            raise VenueError("CREDENTIALS_REQUIRED", "Fyers requires an app id and secret")
        expires = self.session_expires_at()
        expired = expires is not None and expires <= self._clock.now() + timedelta(minutes=1)
        if (not self.access_token or expired) and not self.refresh():
            raise VenueError("LOGIN_REQUIRED", "sign in to Fyers again to continue")

    # ---- transport ----------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        retry: bool = True,
    ) -> dict:
        self._ensure_session()
        self.guard()
        self._per_second.acquire()
        self._per_minute.acquire()
        headers = {"Authorization": f"{self.app_id}:{self.access_token}", "version": "3"}
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
            raise VenueError("RATE_LIMITED", "Fyers rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"Fyers returned {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise VenueError("VENUE_ERROR", f"unexpected Fyers response ({response.status_code})") from None
        if body.get("s") == "ok" and response.status_code < 400:
            self.record_success()
            return body
        code = body.get("code")
        message = str(body.get("message") or f"HTTP {response.status_code}")
        lowered = message.lower()
        token_problem = "token" in lowered and ("expire" in lowered or "invalid" in lowered)
        if response.status_code == 401 or code in AUTH_ERROR_CODES or token_problem:
            if retry and self.refresh():
                return self._request(method, url, params=params, json=json, retry=False)
            raise VenueError("LOGIN_REQUIRED", f"Fyers session is no longer valid: {message}", raw=body)
        raise VenueError(_error_code(message), message, raw=body)

    # ---- connection & reference data ----------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        profile = self._request("GET", f"{API}/profile").get("data") or {}
        balances = self.fetch_balances()
        name = profile.get("name") or profile.get("fy_id") or ""
        # API apps cannot move money out of a Fyers account.
        return ConnectionTest(True, True, True, False, None, f"signed in as {name}".strip(), balances)

    def fetch_balances(self) -> list[Balance]:
        body = self._request("GET", f"{API}/funds")
        rows = {str(r.get("title", "")).lower(): r for r in body.get("fund_limit") or []}
        available = rows.get("available balance") or {}
        total = rows.get("total balance") or {}
        free = _d(available.get("equityAmount", 0))
        locked = max(_d(total.get("equityAmount", 0)) - free, Decimal(0))
        return [Balance("INR", free, locked)]

    def fetch_instruments(self) -> list[Instrument]:
        """NSE equities from Fyers' public symbol master."""
        try:
            response = self._http.get(SYMBOL_MASTER)
        except httpx.HTTPError as exc:
            raise VenueError("VENUE_UNAVAILABLE", f"symbol master download failed: {exc}") from exc
        if response.status_code != 200:
            raise VenueError("VENUE_UNAVAILABLE", f"symbol master returned {response.status_code}")
        instruments = []
        for row in csv.reader(io.StringIO(response.text)):
            if len(row) <= COL_TICKER:
                continue
            ticker = row[COL_TICKER].strip()
            if not ticker.startswith("NSE:") or not ticker.endswith("-EQ"):
                continue
            try:
                lot, tick = _d(row[COL_LOT] or 1), _d(row[COL_TICK] or "0.05")
            except ArithmeticError:
                continue
            symbol = ticker.split(":", 1)[1]
            instruments.append(
                Instrument(
                    venue="NSE",
                    symbol=symbol,
                    asset_class=AssetClass.EQUITY,
                    base_asset=symbol.removesuffix("-EQ"),
                    quote_asset="INR",
                    tick_size=tick,
                    lot_size=lot,
                    min_quantity=lot,
                    status=InstrumentStatus.ACTIVE,
                    aliases=(("FYERS", ticker),),
                )
            )
        return instruments

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        body = self._request("GET", f"{DATA}/quotes", params={"symbols": instrument.instrument_id})
        for item in body.get("d") or []:
            v = item.get("v") or {}
            if item.get("s") != "ok" or not v.get("bid") or not v.get("ask"):
                continue
            bid, ask = _d(v["bid"]), _d(v["ask"])
            if bid >= ask:
                continue
            at = datetime.fromtimestamp(int(v["tt"]), UTC) if v.get("tt") else self._clock.now()
            return Quote(instrument.instrument_id, at, bid, Decimal(0), ask, Decimal(0))
        return None

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        resolution = RESOLUTIONS.get(interval_seconds)
        if resolution is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"Fyers has no {interval_seconds}s resolution")
        daily = resolution == "D"
        # Trading days are ~6.25 hours, 5 days a week: over-fetch calendar time to cover `limit` bars.
        bars_per_day = 1 if daily else max(1, int(22_500 / interval_seconds))
        days_needed = int(limit / bars_per_day * 7 / 5) + 10
        chunk = timedelta(days=365 if daily else 90)
        now = self._clock.now()
        start = now - timedelta(days=days_needed)
        rows: dict[int, list] = {}
        while start < now:
            end = min(start + chunk, now)
            body = self._request(
                "GET",
                f"{DATA}/history",
                params={
                    "symbol": instrument.instrument_id,
                    "resolution": resolution,
                    "date_format": 0,
                    "range_from": int(start.timestamp()),
                    "range_to": int(end.timestamp()),
                    "cont_flag": 1,
                },
            )
            for row in body.get("candles") or []:
                rows[int(row[0])] = row
            start = end
        step = timedelta(seconds=interval_seconds)
        candles = []
        for ts in sorted(rows):
            _, o, h, low, c, v = rows[ts][:6]
            open_ts = datetime.fromtimestamp(ts, UTC)
            close_ts = open_ts + step
            if close_ts > now:
                continue  # the current bar is still forming
            candles.append(
                Candle(instrument.instrument_id, interval_seconds, open_ts, close_ts,
                       _d(o), _d(h), _d(low), _d(c), _d(v or 0))
            )  # fmt: skip
        return candles[-limit:]

    # ---- trading ------------------------------------------------------------------------------

    def _check(self, order: Order) -> str:
        venue, _, symbol = order.instrument_id.partition(":")
        if venue not in self.markets:
            raise VenueError("INSTRUMENT_UNSUPPORTED", f"Fyers trades {', '.join(self.markets)} instruments")
        if order.post_only or order.time_in_force not in VALIDITY or order.order_type not in ORDER_TYPES:
            raise VenueError("ORDER_TYPE_UNSUPPORTED", "unsupported order type, time in force or post-only")
        if order.quantity != order.quantity.to_integral_value():
            raise VenueError("INVALID_QUANTITY", "NSE equity quantities are whole shares")
        return f"{venue}:{symbol}"

    def submit(self, order: Order) -> None:
        try:
            symbol = self._check(order)
            body = {
                "symbol": symbol,
                "qty": _num(order.quantity),
                "type": ORDER_TYPES[order.order_type],
                "side": 1 if order.side is Side.BUY else -1,
                "productType": self.product.value,
                "limitPrice": _num(order.limit_price) if order.limit_price is not None else 0,
                "stopPrice": _num(order.stop_price) if order.stop_price is not None else 0,
                "validity": VALIDITY[order.time_in_force],
                "disclosedQty": 0,
                "offlineOrder": False,
                "orderTag": _tag(order.client_order_id),
            }
            response = self._request("POST", f"{API}/orders/sync", json=body)
        except VenueTimeout:
            return  # the OMS marks the order UNKNOWN and resolves it by querying the order book
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        venue_id = str(response.get("id") or "")
        if not venue_id:
            self.emit(ReportType.REJECT, order, reason="ORDER_REJECTED")
            return
        self._venue_ids[order.client_order_id] = venue_id
        self.emit(ReportType.ACK, order, venue_order_id=venue_id)

    def cancel(self, order: Order) -> None:
        venue_id = self._venue_id(order)
        if venue_id is None:
            self.emit(ReportType.CANCEL_REJECT, order, reason="ORDER_NOT_WORKING")
            return
        try:
            self._request("DELETE", f"{API}/orders/sync", json={"id": venue_id})
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.query(order)

    def replace(self, order: Order, quantity: Decimal, limit_price: Decimal) -> None:
        """Fyers modifies the order in place; the order id and cumulative fills carry over."""
        venue_id = self._venue_id(order)
        try:
            self._request(
                "PATCH",
                f"{API}/orders/sync",
                json={
                    "id": venue_id,
                    "qty": _num(quantity),
                    "type": ORDER_TYPES[order.order_type],
                    "limitPrice": _num(limit_price),
                },
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.REPLACE_REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.REPLACED, order, venue_order_id=venue_id)

    def query(self, order: Order) -> None:
        try:
            self._load_book()
        except VenueTimeout:
            return
        except VenueError:
            return
        self._reconcile_from_book(order)

    def poll(self, orders: list[Order]) -> None:
        live = (S.SUBMITTED, S.UNKNOWN, S.OPEN, S.PARTIALLY_FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE)
        working = [o for o in orders if o.status in live]
        if not working:
            return
        try:
            self._load_book()  # one order-book call covers every working order
        except (VenueTimeout, VenueError):
            return
        for order in working:
            self._reconcile_from_book(order)

    def _load_book(self) -> None:
        body = self._request("GET", f"{API}/orders")
        self._book = {str(o.get("id")): o for o in body.get("orderBook") or []}

    def _venue_id(self, order: Order) -> str | None:
        venue_id = order.venue_order_id or self._venue_ids.get(order.client_order_id)
        if venue_id is None:  # e.g. a timed-out submit: find it by its tag
            tag = _tag(order.client_order_id)
            venue_id = next((i for i, o in self._book.items() if o.get("orderTag") == tag), None)
        return venue_id

    def _reconcile_from_book(self, order: Order) -> None:
        venue_id = self._venue_id(order)
        entry = self._book.get(venue_id) if venue_id else None
        if entry is None:
            if order.status in (S.SUBMITTED, S.UNKNOWN):
                self.emit(ReportType.NOT_FOUND, order)
            return
        self._venue_ids[order.client_order_id] = venue_id
        status = entry.get("status")
        if status == REJECTED:
            self.emit(ReportType.REJECT, order, reason=_error_code(str(entry.get("message", ""))))
            return
        if order.status in (S.SUBMITTED, S.UNKNOWN) and status != TRANSIT:
            self.emit(ReportType.ACK, order, venue_order_id=venue_id)
        filled = _d(entry.get("filledQty") or 0)
        avg = _d(entry.get("tradedPrice") or 0)
        known = (order.filled_quantity, order.average_fill_price or Decimal(0))
        prev_filled, prev_avg = self._seen.get(venue_id, known)
        if filled > prev_filled and avg > 0:
            delta = filled - prev_filled
            price = (avg * filled - prev_avg * prev_filled) / delta
            fee = self.fees.breakdown(order.side, delta * price)["total"]  # Fyers reports no per-fill charges
            self.emit_fill(
                order,
                trade_id=f"{venue_id}:{filled}",
                price=price.quantize(Decimal("0.0001"), ROUND_FLOOR).normalize(),
                quantity=delta,
                fee=fee,
                fee_asset="INR",
                is_maker=None,
                at=_parse_time(entry.get("orderDateTime")) or self._clock.now(),
            )
        self._seen[venue_id] = (filled, avg)
        if status == CANCELLED:
            self.emit(ReportType.CANCELED, order)
        elif status == EXPIRED:
            self.emit(ReportType.EXPIRED, order, reason="expired")


def _tag(client_order_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", client_order_id)[-20:]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=IST).astimezone(UTC)
        except ValueError:
            continue
    return None


def _error_code(message: str) -> str:
    lowered = message.lower()
    if "fund" in lowered or "margin" in lowered or "insufficient" in lowered:
        return "INSUFFICIENT_BALANCE"
    if "qty" in lowered or "quantity" in lowered or "lot" in lowered:
        return "INVALID_QUANTITY"
    if "price" in lowered or "circuit" in lowered or "tick" in lowered:
        return "PRICE_OUT_OF_BAND"
    if "market" in lowered and ("closed" in lowered or "not open" in lowered):
        return "MARKET_CLOSED"
    if "symbol" in lowered or "invalid instrument" in lowered:
        return "INSTRUMENT_UNSUPPORTED"
    return "ORDER_REJECTED"
