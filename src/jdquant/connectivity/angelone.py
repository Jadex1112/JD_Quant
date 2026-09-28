"""Angel One SmartAPI for NSE stocks and ETFs.

Endpoints, headers and the login call follow Angel One's official `smartapi-python` client. SmartAPI
logs in with the client code, PIN and a time-based one-time password, so with the TOTP secret stored
(encrypted, like every credential) the platform signs in by itself each day; no browser step is needed.
Credentials: the SmartAPI key, and a secret holding the client code, PIN and TOTP secret.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

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
from jdquant.security.totp import code_at

API = "https://apiconnect.angelone.in"
SCRIP_MASTER = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
BASE = "/rest/secure/angelbroking"
INTERVALS = {
    60: "ONE_MINUTE",
    180: "THREE_MINUTE",
    300: "FIVE_MINUTE",
    600: "TEN_MINUTE",
    900: "FIFTEEN_MINUTE",
    1800: "THIRTY_MINUTE",
    3600: "ONE_HOUR",
    86400: "ONE_DAY",
}
CHUNK_DAYS = {60: 28, 180: 55, 300: 95, 600: 95, 900: 190, 1800: 190, 3600: 390, 86400: 1900}
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
AUTH_ERRORS = ("AG8001", "AG8002", "AG8003", "AB1010", "AB8050", "AB8051")


class AngelOneAdapter(IndianCashBroker):
    venue = "ANGELONE"
    requires_login = False  # signs in by itself with the TOTP secret
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
                "ENVIRONMENT_UNSUPPORTED", "use a paper account to trade Angel One prices without money"
            )
        try:
            secret = json.loads(api_secret or "{}")
            self.client_code, self._pin, self._totp = (
                secret["client_code"],
                secret["pin"],
                secret["totp_secret"],
            )
        except (ValueError, KeyError, TypeError):
            raise VenueError(
                "CREDENTIALS_REQUIRED", "Angel One needs the SmartAPI key, client code, PIN and TOTP secret"
            ) from None
        self.api_key = api_key or ""
        self._jwt: str | None = None
        self._logged_in_at: datetime | None = None
        self._limiter = TokenBucket(10, 1.0)
        self._history_limiter = TokenBucket(3, 1.0)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.api_key,
        }
        if self._jwt:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    # ---- session --------------------------------------------------------------------------------------

    def login(self) -> None:
        self._jwt = None
        body = self._post_raw(
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            {
                "clientcode": self.client_code,
                "password": self._pin,
                "totp": code_at(self._totp, self._clock.now()),
            },
        )
        if not body.get("status") or not (body.get("data") or {}).get("jwtToken"):
            raise VenueError("API_KEY_INVALID", f"Angel One login failed: {body.get('message')}", raw=body)
        self._jwt = body["data"]["jwtToken"]
        self._logged_in_at = self._clock.now()

    def is_ready(self) -> bool:
        return True  # it can always sign in again on its own

    def _session(self) -> None:
        fresh = self._logged_in_at and self._clock.now() - self._logged_in_at < timedelta(hours=12)
        if not self._jwt or not fresh:
            self.login()

    def _post_raw(self, path: str, body: dict | None = None, method: str = "POST") -> dict:
        response = self._send(method, f"{API}{path}", json=body, headers=self._headers())
        try:
            return response.json()
        except ValueError:
            raise VenueError(
                "VENUE_ERROR", f"unexpected Angel One response ({response.status_code})"
            ) from None

    def _call(self, path: str, body: dict | None = None, method: str = "POST", *, history=False, retry=True):
        self._session()
        (self._history_limiter if history else self._limiter).acquire()
        data = self._post_raw(path, body, method)
        if not data.get("status"):
            code = str(data.get("errorcode") or data.get("errorCode") or "")
            if code in AUTH_ERRORS and retry:
                self._jwt = None
                return self._call(path, body, method, history=history, retry=False)
            message = str(data.get("message") or code or "request failed")
            raise VenueError(
                "ORDER_REJECTED" if "order" in path.lower() else "VENUE_ERROR", message, raw=data
            )
        return data.get("data")

    # ---- account and reference data ------------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        self.login()
        return ConnectionTest(
            True, True, True, False, None, f"Angel One client {self.client_code}", self.fetch_balances()
        )

    def fetch_balances(self) -> list[Balance]:
        rms = self._call(f"{BASE}/user/v1/getRMS", method="GET") or {}
        return [Balance("INR", d(rms.get("availablecash") or rms.get("net")), d(rms.get("utiliseddebits")))]

    def _instruments(self) -> list[Instrument]:
        response = self._send("GET", SCRIP_MASTER)
        if response.status_code != 200:
            raise VenueError("VENUE_UNAVAILABLE", f"Angel One scrip master returned {response.status_code}")
        out = []
        for row in response.json():
            symbol = str(row.get("symbol") or "")
            if row.get("exch_seg") != "NSE" or not symbol.endswith("-EQ") or row.get("instrumenttype"):
                continue
            out.append(
                nse_equity(
                    symbol,
                    name=str(row.get("name") or ""),
                    tick=tick_in_rupees(row.get("tick_size")),
                    lot=d(row.get("lotsize") or 1),
                    source=self.venue,
                    ref=f"{symbol}|{row['token']}",
                )
            )
        return out

    def _quote_item(self, instrument: Instrument) -> dict:
        token = self.ref(instrument.instrument_id).split("|")[1]
        data = (
            self._call(f"{BASE}/market/v1/quote", {"mode": "FULL", "exchangeTokens": {"NSE": [token]}}) or {}
        )
        return next(iter(data.get("fetched") or []), {})

    def fetch_snapshots(self, instruments: list[Instrument]) -> dict[str, dict]:
        out = {}
        for batch in chunks(instruments, 50):
            tokens = {
                self.ref(i.instrument_id).split("|")[1]: i.instrument_id for i in batch if self.has_ref(i)
            }
            if not tokens:
                continue
            data = self._call(
                f"{BASE}/market/v1/quote", {"mode": "FULL", "exchangeTokens": {"NSE": list(tokens)}}
            )
            for item in (data or {}).get("fetched") or []:
                stats = day_stats(
                    item.get("ltp"),
                    item.get("open"),
                    item.get("high"),
                    item.get("low"),
                    item.get("close"),
                    item.get("tradeVolume"),
                    item.get("netChange"),
                )
                if stats is not None and item.get("symbolToken") in tokens:
                    out[tokens[item["symbolToken"]]] = stats
        return out

    def fetch_depth(self, instrument: Instrument):
        item = self._quote_item(instrument)
        depth = item.get("depth") or {}
        return self.book(
            instrument,
            bids=levels(depth.get("buy"), bid=True),
            asks=levels(depth.get("sell"), bid=False),
            at=ist_time(item.get("exchFeedTime")),
            last_price=item.get("ltp"),
            last_quantity=item.get("lastTradeQty"),
            volume=item.get("tradeVolume"),
            open_interest=item.get("opnInterest"),
            total_buy_quantity=item.get("totBuyQuan"),
            total_sell_quantity=item.get("totSellQuan"),
        )

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        item = self._quote_item(instrument)
        depth = item.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        if not buys or not sells or not buys[0].get("price") or not sells[0].get("price"):
            return None
        bid, ask = d(buys[0]["price"]), d(sells[0]["price"])
        if bid >= ask:
            return None
        at = ist_time(item.get("exchFeedTime")) or self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, d(buys[0].get("quantity")), ask, d(sells[0].get("quantity"))
        )

    def _history(self, instrument, ref, interval, start, end) -> list:
        fmt = "%Y-%m-%d %H:%M"
        rows = self._call(
            f"{BASE}/historical/v1/getCandleData",
            {
                "exchange": "NSE",
                "symboltoken": ref.split("|")[1],
                "interval": interval,
                "fromdate": start.astimezone(IST).strftime(fmt),
                "todate": end.astimezone(IST).strftime(fmt),
            },
            history=True,
        )
        return [(ist_time(r[0]), *r[1:6]) for r in rows or []]

    # ---- orders -------------------------------------------------------------------------------------------

    def _place(self, order: Order, ref: str, tag: str) -> str:
        symbol, token = ref.split("|")
        body = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "BUY" if order.side is Side.BUY else "SELL",
            "exchange": "NSE",
            "ordertype": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "producttype": "DELIVERY" if self.product is Product.CNC else "INTRADAY",
            "duration": "IOC" if order.time_in_force.value == "IOC" else "DAY",
            "price": f"{order.limit_price.normalize():f}" if order.order_type is OrderType.LIMIT else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(int(order.quantity)),
            "ordertag": tag,
        }
        return str((self._call(f"{BASE}/order/v1/placeOrder", body) or {}).get("orderid") or "")

    def _cancel(self, venue_id: str) -> None:
        self._call(f"{BASE}/order/v1/cancelOrder", {"variety": "NORMAL", "orderid": venue_id})

    def _orders(self) -> list[BookEntry]:
        return [
            BookEntry(
                str(o["orderid"]),
                STATUS.get(str(o.get("status", "")).lower(), "OPEN"),
                d(o.get("filledshares")),
                d(o.get("averageprice")),
                str(o.get("ordertag") or ""),
                str(o.get("text") or ""),
                ist_time(o.get("exchorderupdatetime") or o.get("updatetime")),
            )
            for o in self._call(f"{BASE}/order/v1/getOrderBook", method="GET") or []
        ]
