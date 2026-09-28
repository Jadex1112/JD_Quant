"""Upstox (API v2/v3) for NSE stocks and ETFs.

Endpoints and fields follow Upstox's official `upstox-python-sdk`: OAuth 2 authorization-code sign-in,
orders on the low-latency order host (v3), the order book and quotes on v2, and v3 historical candles.
Upstox sessions end at 03:30 IST the next morning, so trading needs a daily sign-in.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

import httpx

from jdquant.connectivity.base import Balance, ConnectionTest, Environment, TokenBucket, VenueError
from jdquant.connectivity.india_broker import (
    BookEntry,
    IndianCashBroker,
    d,
    ist_time,
    nse_equity,
    tick_in_rupees,
)
from jdquant.core.clock import Clock
from jdquant.core.types import Side
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Quote
from jdquant.markets.india import IST, Product
from jdquant.oms.orders import Order, OrderType

API = "https://api.upstox.com"
ORDERS = "https://api-hft.upstox.com"
INSTRUMENTS = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INTERVALS = {
    60: ("minutes", 1),
    180: ("minutes", 3),
    300: ("minutes", 5),
    900: ("minutes", 15),
    1800: ("minutes", 30),
    3600: ("hours", 1),
    86400: ("days", 1),
}
CHUNK_DAYS = {60: 28, 180: 28, 300: 28, 900: 28, 1800: 85, 3600: 85, 86400: 3600}
STATUS = {
    "complete": "FILLED",
    "rejected": "REJECTED",
    "cancelled": "CANCELLED",
    "open": "OPEN",
    "trigger pending": "OPEN",
    "validation pending": "PENDING",
    "put order req received": "PENDING",
    "open pending": "PENDING",
    "modify pending": "OPEN",
    "cancel pending": "OPEN",
    "after market order req received": "OPEN",
    "modified": "OPEN",
}


class UpstoxAdapter(IndianCashBroker):
    venue = "UPSTOX"
    requires_login = True
    session_parts = ("access_token", "expires_at")
    intervals = INTERVALS
    history_chunk_days = CHUNK_DAYS

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
        super().__init__(clock, http=http, product=product)
        if environment is not Environment.PRODUCTION:
            raise VenueError(
                "ENVIRONMENT_UNSUPPORTED", "use a paper account to trade Upstox prices without money"
            )
        self.api_key, self._secret = api_key or "", api_secret or ""
        self.access_token: str | None = None
        self.expires_at: datetime | None = None
        self._redirect_uri = ""
        self._limiter = TokenBucket(25, 1.0)

    # ---- login -------------------------------------------------------------------------------------

    def login_url(self, redirect_uri: str, state: str) -> str:
        self._redirect_uri = redirect_uri  # the token exchange must repeat it
        query = urlencode(
            {"response_type": "code", "client_id": self.api_key, "redirect_uri": redirect_uri, "state": state}
        )
        return f"{API}/v2/login/authorization/dialog?{query}"

    def complete_login(self, auth_code: str) -> None:
        response = self._send(
            "POST",
            f"{API}/v2/login/authorization/token",
            data={
                "code": auth_code,
                "client_id": self.api_key,
                "client_secret": self._secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json", "Api-Version": "2.0"},
        )
        body = response.json() if response.content else {}
        if response.status_code >= 400 or not body.get("access_token"):
            raise VenueError("LOGIN_FAILED", _message(body) or f"HTTP {response.status_code}", raw=body)
        self.access_token = body["access_token"]
        now = self._clock.now().astimezone(IST)
        cutoff = time(3, 30)
        self.expires_at = datetime.combine(
            now.date() + timedelta(days=0 if now.time() < cutoff else 1), cutoff, IST
        )
        if self.on_session_changed is not None:
            self.on_session_changed(self.session())

    def set_session(self, session: dict[str, str | None]) -> None:
        self.access_token = session.get("access_token")
        expires = session.get("expires_at")
        self.expires_at = datetime.fromisoformat(expires) if expires else None

    def session(self) -> dict[str, str | None]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def session_expires_at(self) -> datetime | None:
        return self.expires_at

    def is_ready(self) -> bool:
        return bool(self.access_token) and (self.expires_at is None or self.expires_at > self._clock.now())

    # ---- transport -----------------------------------------------------------------------------------

    def _call(self, method: str, url: str, *, params=None, json_body=None):
        if not self.is_ready():
            raise VenueError("LOGIN_REQUIRED", "sign in to Upstox again to continue")
        self._limiter.acquire()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }
        response = self._send(method, url, params=params, json=json_body, headers=headers)
        try:
            body = response.json()
        except ValueError:
            raise VenueError("VENUE_ERROR", f"unexpected Upstox response ({response.status_code})") from None
        if response.status_code == 401:
            self.access_token = None
            raise VenueError(
                "LOGIN_REQUIRED", f"Upstox session is no longer valid: {_message(body)}", raw=body
            )
        if response.status_code >= 400 or body.get("status") == "error":
            message = _message(body) or f"HTTP {response.status_code}"
            raise VenueError("ORDER_REJECTED" if "order" in url else "VENUE_ERROR", message, raw=body)
        return body.get("data")

    # ---- account and reference data ------------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        profile = self._call("GET", f"{API}/v2/user/profile") or {}
        return ConnectionTest(
            True,
            True,
            bool(profile.get("is_active", True)),
            False,
            None,
            f"Upstox user {profile.get('user_id', '')}",
            self.fetch_balances(),
        )

    def fetch_balances(self) -> list[Balance]:
        equity = (
            self._call("GET", f"{API}/v2/user/get-funds-and-margin", params={"segment": "SEC"}) or {}
        ).get("equity") or {}
        return [Balance("INR", d(equity.get("available_margin")), d(equity.get("used_margin")))]

    def _instruments(self) -> list[Instrument]:
        response = self._send("GET", INSTRUMENTS)
        if response.status_code != 200:
            raise VenueError("VENUE_UNAVAILABLE", f"Upstox instruments returned {response.status_code}")
        raw = response.content
        try:
            rows = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        except (OSError, ValueError):
            raise VenueError("VENUE_UNAVAILABLE", "Upstox instruments file is not readable") from None
        out = []
        for row in rows:
            if row.get("segment") != "NSE_EQ" or row.get("instrument_type") != "EQ":
                continue
            symbol = str(row.get("trading_symbol") or "")
            if not symbol or not symbol.isascii() or "-" in symbol:
                continue
            out.append(
                nse_equity(
                    symbol,
                    name=str(row.get("name") or ""),
                    tick=tick_in_rupees(row.get("tick_size")),
                    lot=d(row.get("lot_size") or 1),
                    source=self.venue,
                    ref=str(row["instrument_key"]),
                )
            )
        return out

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        key = self.ref(instrument.instrument_id)
        data = (
            self._call("GET", f"{API}/v2/market-quote/quotes", params={"instrument_key": key, "symbol": key})
            or {}
        )
        item = next((v for v in data.values() if v.get("instrument_token") == key), None) or next(
            iter(data.values()), {}
        )
        depth = item.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        if not buys or not sells or not buys[0].get("price") or not sells[0].get("price"):
            return None
        bid, ask = d(buys[0]["price"]), d(sells[0]["price"])
        if bid >= ask:
            return None
        at = ist_time(item.get("timestamp")) or self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, d(buys[0].get("quantity")), ask, d(sells[0].get("quantity"))
        )

    def _history(self, instrument, ref, interval, start, end) -> list:
        unit, size = interval
        to_date, from_date = end.astimezone(IST).date().isoformat(), start.astimezone(IST).date().isoformat()
        path = f"{API}/v3/historical-candle/{ref}/{unit}/{size}/{to_date}/{from_date}"
        data = self._call("GET", path) or {}
        return [(ist_time(r[0]), *r[1:6]) for r in data.get("candles") or []]

    # ---- orders -------------------------------------------------------------------------------------------

    def _place(self, order: Order, ref: str, tag: str) -> str:
        body = {
            "instrument_token": ref,
            "quantity": int(order.quantity),
            "product": "D" if self.product is Product.CNC else "I",
            "validity": "IOC" if order.time_in_force.value == "IOC" else "DAY",
            "price": float(order.limit_price) if order.order_type is OrderType.LIMIT else 0,
            "order_type": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "transaction_type": "BUY" if order.side is Side.BUY else "SELL",
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": False,
            "tag": tag,
        }
        ids = (self._call("POST", f"{ORDERS}/v3/order/place", json_body=body) or {}).get("order_ids") or []
        return str(ids[0]) if ids else ""

    def _cancel(self, venue_id: str) -> None:
        self._call("DELETE", f"{ORDERS}/v3/order/cancel", params={"order_id": venue_id})

    def _orders(self) -> list[BookEntry]:
        return [
            BookEntry(
                str(o["order_id"]),
                STATUS.get(str(o.get("status", "")).lower(), "OPEN"),
                d(o.get("filled_quantity")),
                d(o.get("average_price")),
                str(o.get("tag") or ""),
                str(o.get("status_message") or ""),
                ist_time(o.get("exchange_timestamp") or o.get("order_timestamp")),
            )
            for o in self._call("GET", f"{API}/v2/order/retrieve-all") or []
        ]


def _message(body: dict) -> str:
    errors = body.get("errors") or []
    if errors and isinstance(errors, list) and isinstance(errors[0], dict):
        return str(errors[0].get("message") or errors[0].get("errorCode") or "")
    return str(body.get("message") or "")
