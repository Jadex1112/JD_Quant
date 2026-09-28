"""Zerodha Kite Connect (API v3) for NSE stocks and ETFs.

Endpoints, headers and the login checksum follow Zerodha's official `pykiteconnect` client. Sign-in is a
browser redirect: Kite sends the user back with a `request_token`, which is exchanged for an access token
using a SHA-256 checksum of api_key + request_token + api_secret. Kite sessions end at 06:00 IST the next
morning, so trading on Kite needs a daily sign-in.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

import httpx

from jdquant.connectivity.base import Balance, ConnectionTest, Environment, TokenBucket, VenueError
from jdquant.connectivity.india_broker import (
    BookEntry,
    IndianCashBroker,
    chunks,
    d,
    day_stats,
    ist_time,
    nse_equity,
    tick_in_rupees,
)
from jdquant.core.clock import Clock
from jdquant.core.types import Side
from jdquant.marketdata.book import levels
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Quote
from jdquant.markets.india import IST, Product
from jdquant.oms.orders import Order, OrderType

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
INTERVALS = {
    60: "minute",
    180: "3minute",
    300: "5minute",
    600: "10minute",
    900: "15minute",
    1800: "30minute",
    3600: "60minute",
    86400: "day",
}
# Kite's limits on the span of one historical request.
CHUNK_DAYS = {60: 55, 180: 95, 300: 95, 600: 95, 900: 190, 1800: 190, 3600: 390, 86400: 1900}
STATUS = {
    "COMPLETE": "FILLED",
    "REJECTED": "REJECTED",
    "CANCELLED": "CANCELLED",
    "OPEN": "OPEN",
    "TRIGGER PENDING": "OPEN",
    "OPEN PENDING": "PENDING",
    "VALIDATION PENDING": "PENDING",
    "PUT ORDER REQ RECEIVED": "PENDING",
    "MODIFY PENDING": "OPEN",
    "CANCEL PENDING": "OPEN",
    "MODIFY VALIDATION PENDING": "OPEN",
    "AMO REQ RECEIVED": "OPEN",
}


class KiteAdapter(IndianCashBroker):
    venue = "KITE"
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
            raise VenueError("ENVIRONMENT_UNSUPPORTED", "Kite has no test environment; use a paper account")
        self.api_key, self._secret = api_key or "", api_secret or ""
        self.access_token: str | None = None
        self.expires_at: datetime | None = None
        self._limiter = TokenBucket(10, 1.0)
        self._history_limiter = TokenBucket(3, 1.0)

    # ---- login -------------------------------------------------------------------------------------

    def login_url(self, redirect_uri: str, state: str) -> str:
        # The redirect URL is set on the Kite app; redirect_params come back with the request token.
        query = urlencode({"v": 3, "api_key": self.api_key})
        return f"{LOGIN}?{query}&redirect_params={quote(urlencode({'state': state}))}"

    def complete_login(self, auth_code: str) -> None:
        checksum = hashlib.sha256(f"{self.api_key}{auth_code}{self._secret}".encode()).hexdigest()
        response = self._send(
            "POST",
            f"{API}/session/token",
            data={"api_key": self.api_key, "request_token": auth_code, "checksum": checksum},
            headers={"X-Kite-Version": "3"},
        )
        data = self._data(response, login=True)
        self.access_token = data["access_token"]
        now = self._clock.now().astimezone(IST)
        next_six = datetime.combine(
            now.date() + timedelta(days=1 if now.time() >= time(6) else 0), time(6), IST
        )
        self.expires_at = next_six
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

    def _call(self, method: str, path: str, *, params=None, data=None, history: bool = False):
        if not self.is_ready():
            raise VenueError("LOGIN_REQUIRED", "sign in to Kite again to continue")
        (self._history_limiter if history else self._limiter).acquire()
        headers = {"X-Kite-Version": "3", "Authorization": f"token {self.api_key}:{self.access_token}"}
        response = self._send(method, f"{API}{path}", params=params, data=data, headers=headers)
        if "csv" in response.headers.get("content-type", ""):
            return response.text
        return self._data(response)

    def _data(self, response: httpx.Response, login: bool = False):
        try:
            body = response.json()
        except ValueError:
            raise VenueError("VENUE_ERROR", f"unexpected Kite response ({response.status_code})") from None
        if body.get("status") == "error" or response.status_code >= 400:
            message = str(body.get("message") or f"HTTP {response.status_code}")
            if body.get("error_type") == "TokenException" or response.status_code == 403:
                self.access_token = None
                raise VenueError("LOGIN_FAILED" if login else "LOGIN_REQUIRED", f"Kite: {message}", raw=body)
            raise VenueError(
                "ORDER_REJECTED" if body.get("error_type") == "OrderException" else "VENUE_ERROR",
                message,
                raw=body,
            )
        return body.get("data")

    # ---- account and reference data ---------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        profile = self._call("GET", "/user/profile")
        return ConnectionTest(
            True, True, True, False, None, f"Kite user {profile.get('user_id', '')}", self.fetch_balances()
        )

    def fetch_balances(self) -> list[Balance]:
        equity = (self._call("GET", "/user/margins") or {}).get("equity") or {}
        available = equity.get("available") or {}
        free = d(available.get("live_balance", available.get("cash", equity.get("net", 0))))
        used = d((equity.get("utilised") or {}).get("debits", 0))
        return [Balance("INR", free, used)]

    def _instruments(self) -> list[Instrument]:
        text = self._call("GET", "/instruments/NSE")
        out = []
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("instrument_type") != "EQ" or row.get("segment") != "NSE":
                continue
            symbol = row["tradingsymbol"]
            if not symbol.isascii() or "-" in symbol:  # e.g. SME and other series
                continue
            out.append(
                nse_equity(
                    symbol,
                    name=row.get("name", ""),
                    tick=tick_in_rupees(row.get("tick_size")),
                    lot=d(row.get("lot_size") or 1),
                    source=self.venue,
                    ref=f"{symbol}|{row['instrument_token']}",
                )
            )
        return out

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        symbol = self.ref(instrument.instrument_id).split("|")[0]
        key = f"NSE:{symbol}"
        data = (self._call("GET", "/quote", params={"i": key}) or {}).get(key) or {}
        depth = data.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        if not buys or not sells or not buys[0].get("price") or not sells[0].get("price"):
            return None
        bid, ask = d(buys[0]["price"]), d(sells[0]["price"])
        if bid >= ask:
            return None
        at = ist_time(data.get("timestamp")) or self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, d(buys[0].get("quantity")), ask, d(sells[0].get("quantity"))
        )

    def fetch_snapshots(self, instruments: list[Instrument]) -> dict[str, dict]:
        out = {}
        for batch in chunks(instruments, 250):
            keys = {
                f"NSE:{self.ref(i.instrument_id).split('|')[0]}": i.instrument_id
                for i in batch
                if self.has_ref(i)
            }
            if not keys:
                continue
            data = self._call("GET", "/quote", params=[("i", k) for k in keys]) or {}
            for key, item in data.items():
                ohlc = item.get("ohlc") or {}
                stats = day_stats(
                    item.get("last_price"),
                    ohlc.get("open"),
                    ohlc.get("high"),
                    ohlc.get("low"),
                    ohlc.get("close"),
                    item.get("volume"),
                    item.get("net_change"),
                )
                if stats is not None and key in keys:
                    out[keys[key]] = stats
        return out

    def fetch_depth(self, instrument: Instrument):
        symbol = self.ref(instrument.instrument_id).split("|")[0]
        key = f"NSE:{symbol}"
        data = (self._call("GET", "/quote", params={"i": key}) or {}).get(key) or {}
        depth = data.get("depth") or {}
        return self.book(
            instrument,
            bids=levels(depth.get("buy"), bid=True),
            asks=levels(depth.get("sell"), bid=False),
            at=ist_time(data.get("timestamp")),
            last_price=data.get("last_price"),
            last_quantity=data.get("last_quantity"),
            volume=data.get("volume"),
            open_interest=data.get("oi"),
            total_buy_quantity=data.get("buy_quantity"),
            total_sell_quantity=data.get("sell_quantity"),
        )

    def _history(self, instrument, ref, interval, start, end) -> list:
        token = ref.split("|")[1]
        fmt = "%Y-%m-%d %H:%M:%S"
        data = self._call(
            "GET",
            f"/instruments/historical/{token}/{interval}",
            params={"from": start.astimezone(IST).strftime(fmt), "to": end.astimezone(IST).strftime(fmt)},
            history=True,
        )
        return [(ist_time(r[0]), *r[1:6]) for r in (data or {}).get("candles") or []]

    # ---- orders -------------------------------------------------------------------------------------------

    def _place(self, order: Order, ref: str, tag: str) -> str:
        form = {
            "exchange": "NSE",
            "tradingsymbol": ref.split("|")[0],
            "transaction_type": "BUY" if order.side is Side.BUY else "SELL",
            "quantity": str(int(order.quantity)),
            "product": "CNC" if self.product is Product.CNC else "MIS",
            "order_type": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "validity": "IOC" if order.time_in_force.value == "IOC" else "DAY",
            "tag": tag,
        }
        if order.order_type is OrderType.LIMIT:
            form["price"] = f"{order.limit_price.normalize():f}"
        return str((self._call("POST", "/orders/regular", data=form) or {}).get("order_id") or "")

    def _cancel(self, venue_id: str) -> None:
        self._call("DELETE", f"/orders/regular/{venue_id}")

    def _orders(self) -> list[BookEntry]:
        return [
            BookEntry(
                str(o["order_id"]),
                STATUS.get(str(o.get("status", "")).upper(), "OPEN"),
                d(o.get("filled_quantity")),
                d(o.get("average_price")),
                str(o.get("tag") or ""),
                str(o.get("status_message") or ""),
                ist_time(o.get("exchange_timestamp") or o.get("order_timestamp")),
            )
            for o in self._call("GET", "/orders") or []
        ]
