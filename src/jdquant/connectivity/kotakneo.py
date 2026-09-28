"""Kotak Neo Trade API for NSE stocks and ETFs, with the SFeed WebSocket for live books.

Endpoints, headers and payload keys follow Kotak's official `kotakneoapi` SDK (neo_api_client 3.x):

- **Login** is two calls: `tradeApiLogin` with the mobile number, client code (UCC) and a TOTP, then
  `tradeApiValidate` with the MPIN. With the TOTP secret and MPIN stored (encrypted), the platform
  signs in by itself each day, like Angel One. The validate response names the account's base URL and
  its market-data feed URL.
- **Orders** are form posts with the payload JSON under `jData`, authenticated by the session's
  `Sid`/`Auth` headers plus the consumer key.
- **Prices** stream over the SFeed WebSocket (`connectivity.feeds.neo`); the REST quote endpoint is a
  fallback. Kotak does not publish its REST history format, so chart history for Neo instruments comes
  from another connected broker.

Credentials: the consumer key from the Neo app (Trade API card), and a secret holding the mobile
number, UCC, MPIN and TOTP secret.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

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
from jdquant.marketdata.records import Candle, Quote
from jdquant.markets.india import Product
from jdquant.oms.orders import Order, OrderType
from jdquant.security.totp import code_at

SESSION_HOST = "https://mis.kotaksecurities.com"
LOGIN_PATH = "/login/1.0/tradeApiLogin"
VALIDATE_PATH = "/login/1.0/tradeApiValidate"
DEFAULT_BASE = "https://mis.kotaksecurities.com"
DEFAULT_FEED = "wss://sfeed.kotaksecurities.com/apifeed"
FIN_KEY = "neotradeapi"
ORDER_SOURCE = "NEOTRADEAPI"
STATUS = {
    "complete": "FILLED",
    "rejected": "REJECTED",
    "cancelled": "CANCELLED",
    "open": "OPEN",
    "modified": "OPEN",
    "trigger pending": "OPEN",
    "open pending": "PENDING",
    "validation pending": "PENDING",
    "put order req received": "PENDING",
    "modify pending": "OPEN",
    "cancel pending": "OPEN",
}


class KotakNeoAdapter(IndianCashBroker):
    venue = "KOTAKNEO"
    requires_login = False  # signs in by itself with the TOTP secret and MPIN
    intervals = {}
    history_chunk_days = {}

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
                "ENVIRONMENT_UNSUPPORTED", "use a paper account to trade Kotak Neo prices without money"
            )
        try:
            secret = json.loads(api_secret or "{}")
            self.mobile, self.ucc = str(secret["mobile"]), str(secret["ucc"])
            self._mpin, self._totp = str(secret["mpin"]), str(secret["totp_secret"])
        except (ValueError, KeyError, TypeError):
            raise VenueError(
                "CREDENTIALS_REQUIRED",
                "Kotak Neo needs the consumer key, mobile number, client code (UCC), MPIN and TOTP secret",
            ) from None
        if not api_key:
            raise VenueError("CREDENTIALS_REQUIRED", "Kotak Neo needs the consumer key from the Neo app")
        self.consumer_key = api_key
        self._sid: str | None = None
        self._token: str | None = None
        self.base_url = DEFAULT_BASE
        self.feed_url = DEFAULT_FEED
        self._logged_in_at: datetime | None = None
        self._limiter = TokenBucket(10, 1.0)

    # ---- session --------------------------------------------------------------------------------------

    def login(self) -> None:
        self._sid = self._token = None
        body = self._json(
            self._send(
                "POST",
                SESSION_HOST + LOGIN_PATH,
                json={
                    "mobileNumber": self.mobile,
                    "ucc": self.ucc,
                    "totp": code_at(self._totp, self._clock.now()),
                },
                headers={"Authorization": self.consumer_key, "neo-fin-key": FIN_KEY},
            )
        )
        view = body.get("data") or {}
        if not view.get("token") or not view.get("sid"):
            raise VenueError("API_KEY_INVALID", f"Kotak Neo login failed: {_message(body)}", raw=body)
        body = self._json(
            self._send(
                "POST",
                SESSION_HOST + VALIDATE_PATH,
                json={"mpin": self._mpin},
                headers={
                    "Authorization": self.consumer_key,
                    "sid": view["sid"],
                    "Auth": view["token"],
                    "neo-fin-key": FIN_KEY,
                },
            )
        )
        data = body.get("data") or {}
        if not data.get("token") or not data.get("sid"):
            raise VenueError("API_KEY_INVALID", f"Kotak Neo MPIN check failed: {_message(body)}", raw=body)
        self._token, self._sid = data["token"], data["sid"]
        self.ucc = data.get("ucc") or self.ucc
        self.base_url = (data.get("baseUrl") or DEFAULT_BASE).rstrip("/")
        self.feed_url = data.get("feedUrl") or DEFAULT_FEED
        self._logged_in_at = self._clock.now()

    def is_ready(self) -> bool:
        return True  # it can always sign in again on its own

    def _session(self) -> None:
        fresh = self._logged_in_at and self._clock.now() - self._logged_in_at < timedelta(hours=12)
        if not self._token or not fresh:
            self.login()

    def feed_credentials(self) -> dict[str, str]:
        """What the SFeed WebSocket authenticates with (user = UCC, auth = session id)."""
        self._session()
        return {"url": self.feed_url, "user": self.ucc, "auth": self._sid or ""}

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            raise VenueError(
                "VENUE_ERROR", f"unexpected Kotak Neo response ({response.status_code})"
            ) from None
        return body if isinstance(body, dict) else {"data": body}

    def _call(self, method: str, path: str, *, form: dict | None = None, consumer: bool = False, retry=True):
        """Session-authenticated call; form bodies go as `jData` (the Kotak convention)."""
        self._session()
        self._limiter.acquire()
        headers = {"Sid": self._sid or "", "Auth": self._token or "", "accept": "application/json"}
        if consumer:
            headers["Authorization"] = self.consumer_key
        kwargs: dict[str, Any] = {"headers": headers}
        if form is not None:
            kwargs["data"] = {"jData": json.dumps(form)}
        response = self._send(method, f"{self.base_url}{path}", **kwargs)
        body = self._json(response)
        if response.status_code in (401, 403) or str(body.get("stCode")) in ("900901", "900902"):
            if retry:
                self._token = None
                return self._call(method, path, form=form, consumer=consumer, retry=False)
            raise VenueError("LOGIN_REQUIRED", f"Kotak Neo session rejected: {_message(body)}", raw=body)
        if response.status_code >= 400 or str(body.get("stat", "Ok")).lower() == "not_ok":
            code = "ORDER_REJECTED" if "/order" in path else "VENUE_ERROR"
            raise VenueError(code, _message(body) or f"HTTP {response.status_code}", raw=body)
        return body

    # ---- account and reference data ------------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        self.login()
        return ConnectionTest(
            True, True, True, False, None, f"Kotak Neo client {self.ucc}", self.fetch_balances()
        )

    def fetch_balances(self) -> list[Balance]:
        body = self._call("POST", "/quick/user/limits", form={"seg": "ALL", "exch": "ALL", "prod": "ALL"})
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        net = next(
            (data.get(k) for k in ("Net", "net", "NetBalance", "CashBal") if data.get(k) not in (None, "")), 0
        )
        used = next((data.get(k) for k in ("MarginUsed", "marginUsed", "UtilizedAmount") if data.get(k)), 0)
        return [Balance("INR", d(net), d(used))]

    def _instruments(self) -> list[Instrument]:
        self._session()
        response = self._send(
            "GET",
            f"{self.base_url}/script-details/1.0/masterscrip/file-paths",
            headers={"Authorization": self.consumer_key},
        )
        files = ((self._json(response).get("data") or {}).get("filesPaths")) or []
        path = next((f for f in files if "nse_cm" in f.lower()), None)
        if path is None:
            raise VenueError("VENUE_UNAVAILABLE", "Kotak Neo scrip master has no NSE cash file")
        master = self._send("GET", path)
        if master.status_code != 200:
            raise VenueError("VENUE_UNAVAILABLE", f"Kotak Neo scrip master returned {master.status_code}")
        out = []
        for raw in csv.DictReader(io.StringIO(master.text)):
            row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
            symbol, token = str(row.get("pTrdSymbol") or ""), str(row.get("pSymbol") or "")
            if not symbol.endswith("-EQ") or not token or row.get("pGroup") not in (None, "", "EQ"):
                continue
            out.append(
                nse_equity(
                    symbol,
                    name=str(row.get("pSymbolName") or ""),
                    tick=tick_in_rupees(row.get("dTickSize")),
                    lot=d(row.get("lLotSize") or 1),
                    source=self.venue,
                    ref=f"{symbol}|{token}",
                )
            )
        return out

    def _quote_items(self, tokens: list[str]) -> list[dict]:
        self._session()
        symbols = quote(",".join(f"nse_cm|{t}" for t in tokens), safe="|,")
        response = self._send(
            "GET",
            f"{self.base_url}/script-details/1.0/quotes/neosymbol/{symbols}/all",
            headers={"Authorization": self.consumer_key},
        )
        body = response.json() if response.content else []
        items = body.get("data") if isinstance(body, dict) else body
        return [i for i in items or [] if isinstance(i, dict)]

    def fetch_depth(self, instrument: Instrument):
        token = self.ref(instrument.instrument_id).split("|")[1]
        item = next(iter(self._quote_items([token])), {})
        depth = item.get("depth") or {}
        return self.book(
            instrument,
            bids=levels(depth.get("buy"), bid=True),
            asks=levels(depth.get("sell"), bid=False),
            at=ist_time(item.get("lstup_time") or item.get("last_traded_time")),
            last_price=item.get("ltp") or item.get("last_traded_price"),
            last_quantity=item.get("last_traded_quantity"),
            volume=item.get("last_volume") or item.get("volume"),
            open_interest=item.get("open_int") or item.get("open_interest"),
            total_buy_quantity=item.get("total_buy") or item.get("total_buy_quantity"),
            total_sell_quantity=item.get("total_sell") or item.get("total_sell_quantity"),
        )

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        book = self.fetch_depth(instrument)
        return book.quote() if book is not None else None

    def fetch_snapshots(self, instruments: list[Instrument]) -> dict[str, dict]:
        out = {}
        for batch in chunks([i for i in instruments if self.has_ref(i)], 50):
            by_token = {self.ref(i.instrument_id).split("|")[1]: i.instrument_id for i in batch}
            for item in self._quote_items(list(by_token)):
                ohlc = item.get("ohlc") or {}
                token = str(item.get("exchange_token") or item.get("instrument_token") or "")
                stats = day_stats(
                    item.get("ltp"),
                    ohlc.get("open"),
                    ohlc.get("high"),
                    ohlc.get("low"),
                    ohlc.get("close"),
                    item.get("last_volume"),
                    item.get("change"),
                )
                if stats is not None and token in by_token:
                    out[by_token[token]] = stats
        return out

    def fetch_positions(self):
        """Day and carried-forward positions (net of buys and sells). Demat holdings are not included."""
        refs = self.by_ref(0)
        out: dict = {}
        for p in self._call("GET", "/quick/user/positions").get("data") or []:
            if not isinstance(p, dict):
                continue
            iid = refs.get(str(p.get("trdSym") or p.get("sym") or ""))
            net = sum(
                int(float(p.get(k) or 0)) * s
                for k, s in (("flBuyQty", 1), ("cfBuyQty", 1), ("flSellQty", -1), ("cfSellQty", -1))
            )
            self.add_quantity(out, iid, net)
        return out

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        raise VenueError(
            "INTERVAL_UNSUPPORTED",
            "Kotak Neo's history format is not published; charts use another connected broker's history",
        )

    # ---- orders -------------------------------------------------------------------------------------------

    def _place(self, order: Order, ref: str, tag: str) -> str:
        symbol = ref.split("|")[0]
        limit = order.order_type is OrderType.LIMIT
        body = self._call(
            "POST",
            "/quick/order/rule/ms/place",
            consumer=True,
            form={
                "am": "NO",
                "dq": "0",
                "es": "nse_cm",
                "mp": "0",
                "pc": "CNC" if self.product is Product.CNC else "MIS",
                "pr": f"{order.limit_price.normalize():f}" if limit else "0",
                "pt": "L" if limit else "MKT",
                "qt": str(int(order.quantity)),
                "rt": "IOC" if order.time_in_force.value == "IOC" else "DAY",
                "tp": "0",
                "ts": symbol,
                "tt": "B" if order.side is Side.BUY else "S",
                "ig": tag,
                "os": ORDER_SOURCE,
            },
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        order_no = body.get("nOrdNo") or data.get("nOrdNo")
        if not order_no:
            raise VenueError(
                "ORDER_REJECTED", _message(body) or "Kotak Neo returned no order number", raw=body
            )
        return str(order_no)

    def _cancel(self, venue_id: str) -> None:
        self._call("POST", "/quick/order/cancel", consumer=True, form={"on": venue_id, "am": "NO"})

    def _orders(self) -> list[BookEntry]:
        body = self._call("GET", "/quick/user/orders")
        rows = body.get("data") or []
        return [
            BookEntry(
                str(o["nOrdNo"]),
                STATUS.get(str(o.get("ordSt", "")).lower(), "OPEN"),
                d(o.get("fldQty")),
                d(o.get("avgPrc")),
                str(o.get("GuiOrdId") or o.get("rmk") or o.get("ig") or ""),
                "" if str(o.get("rejRsn") or "").strip() in ("", "--") else str(o["rejRsn"]),
                ist_time(o.get("hsUpTm") or o.get("ordDtTm")),
            )
            for o in rows
            if isinstance(o, dict) and o.get("nOrdNo")
        ]


def _message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("errMsg") or body.get("message") or body.get("emsg") or body.get("stCode") or "")
