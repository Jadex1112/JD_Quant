"""Dhan (DhanHQ API v2) for NSE stocks and ETFs.

Endpoints, headers and payloads follow Dhan's official `dhanhq` client. Dhan authenticates each request
with the client id and an access token generated on web.dhan.co (valid for 24 hours): when it expires,
paste a new one with "Rotate credentials" on the connection.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import httpx

from jdquant.connectivity.base import Balance, ConnectionTest, Environment, TokenBucket, VenueError
from jdquant.connectivity.fyers import token_expiry
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

API = "https://api.dhan.co/v2"
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
INTERVALS = {60: "1", 300: "5", 900: "15", 1500: "25", 3600: "60", 86400: "D"}
CHUNK_DAYS = {60: 85, 300: 85, 900: 85, 1500: 85, 3600: 85, 86400: 3000}
STATUS = {
    "TRADED": "FILLED",
    "REJECTED": "REJECTED",
    "CANCELLED": "CANCELLED",
    "PENDING": "OPEN",
    "PART_TRADED": "OPEN",
    "TRANSIT": "PENDING",
    "EXPIRED": "EXPIRED",
    "CONFIRM": "OPEN",
}


class DhanAdapter(IndianCashBroker):
    venue = "DHAN"
    requires_login = False
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
                "ENVIRONMENT_UNSUPPORTED", "use a paper account to trade Dhan prices without money"
            )
        if not (api_key and api_secret):
            raise VenueError("CREDENTIALS_REQUIRED", "Dhan needs your client id and an access token")
        self.client_id, self._token = api_key, api_secret
        self._limiter = TokenBucket(10, 1.0)

    def session_expires_at(self) -> datetime | None:
        return token_expiry(self._token)

    def is_ready(self) -> bool:
        expires = self.session_expires_at()
        return expires is None or expires > self._clock.now()

    def _call(self, method: str, path: str, body: dict | None = None):
        if not self.is_ready():
            raise VenueError("LOGIN_REQUIRED", "the Dhan access token expired; rotate it on the connection")
        self._limiter.acquire()
        headers = {
            "access-token": self._token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if body is not None:
            body = {**body, "dhanClientId": self.client_id}
        response = self._send(method, f"{API}{path}", json=body, headers=headers)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            raise VenueError("VENUE_ERROR", f"unexpected Dhan response ({response.status_code})") from None
        if response.status_code in (401, 403):
            raise VenueError("LOGIN_REQUIRED", f"Dhan rejected the access token: {_message(data)}", raw=data)
        if response.status_code >= 400:
            code = "ORDER_REJECTED" if path.startswith("/orders") else "VENUE_ERROR"
            raise VenueError(code, _message(data) or f"HTTP {response.status_code}", raw=data)
        return data

    # ---- account and reference data ------------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        balances = self.fetch_balances()
        return ConnectionTest(True, True, True, False, None, f"Dhan client {self.client_id}", balances)

    def fetch_balances(self) -> list[Balance]:
        funds = self._call("GET", "/fundlimit") or {}
        available = funds.get(
            "availabelBalance", funds.get("availableBalance")
        )  # Dhan's field name is misspelt
        return [Balance("INR", d(available), d(funds.get("utilizedAmount")))]

    def _instruments(self) -> list[Instrument]:
        response = self._send("GET", SCRIP_MASTER)
        if response.status_code != 200:
            raise VenueError("VENUE_UNAVAILABLE", f"Dhan scrip master returned {response.status_code}")
        out = []
        for row in csv.DictReader(io.StringIO(response.text)):
            if (
                row.get("SEM_EXM_EXCH_ID") != "NSE"
                or row.get("SEM_SEGMENT") != "E"
                or row.get("SEM_INSTRUMENT_NAME") != "EQUITY"
                or row.get("SEM_SERIES", "EQ") != "EQ"
            ):
                continue
            symbol = str(row.get("SEM_TRADING_SYMBOL") or "")
            if not symbol or not symbol.isascii() or "-" in symbol:
                continue
            out.append(
                nse_equity(
                    symbol,
                    name=str(row.get("SM_SYMBOL_NAME") or row.get("SEM_CUSTOM_SYMBOL") or ""),
                    tick=tick_in_rupees(row.get("SEM_TICK_SIZE")),
                    lot=d(row.get("SEM_LOT_UNITS") or 1),
                    source=self.venue,
                    ref=str(row["SEM_SMST_SECURITY_ID"]).split(".")[0],
                )
            )
        return out

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        security_id = self.ref(instrument.instrument_id)
        body = self._call("POST", "/marketfeed/quote", {"NSE_EQ": [int(security_id)]}) or {}
        item = ((body.get("data") or {}).get("NSE_EQ") or {}).get(security_id) or {}
        depth = item.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        if not buys or not sells or not buys[0].get("price") or not sells[0].get("price"):
            return None
        bid, ask = d(buys[0]["price"]), d(sells[0]["price"])
        if bid >= ask:
            return None
        at = ist_time(item.get("last_trade_time")) or self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, d(buys[0].get("quantity")), ask, d(sells[0].get("quantity"))
        )

    def _history(self, instrument, ref, interval, start, end) -> list:
        common = {"securityId": ref, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY"}
        if interval == "D":
            body = {
                **common,
                "expiryCode": 0,
                "fromDate": start.astimezone(IST).date().isoformat(),
                "toDate": end.astimezone(IST).date().isoformat(),
            }
            data = self._call("POST", "/charts/historical", body) or {}
        else:
            fmt = "%Y-%m-%d %H:%M:%S"
            body = {
                **common,
                "interval": interval,
                "oi": False,
                "fromDate": start.astimezone(IST).strftime(fmt),
                "toDate": end.astimezone(IST).strftime(fmt),
            }
            data = self._call("POST", "/charts/intraday", body) or {}
        times = data.get("timestamp") or []
        return [
            (datetime.fromtimestamp(int(t), UTC), o, h, low, c, v)
            for t, o, h, low, c, v in zip(
                times,
                data.get("open") or [],
                data.get("high") or [],
                data.get("low") or [],
                data.get("close") or [],
                data.get("volume") or [],
                strict=False,
            )
        ]

    # ---- orders -------------------------------------------------------------------------------------------

    def _place(self, order: Order, ref: str, tag: str) -> str:
        body = {
            "correlationId": tag,
            "transactionType": "BUY" if order.side is Side.BUY else "SELL",
            "exchangeSegment": "NSE_EQ",
            "productType": "CNC" if self.product is Product.CNC else "INTRADAY",
            "orderType": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "validity": "IOC" if order.time_in_force.value == "IOC" else "DAY",
            "securityId": ref,
            "quantity": int(order.quantity),
            "disclosedQuantity": 0,
            "price": float(order.limit_price) if order.order_type is OrderType.LIMIT else 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        return str((self._call("POST", "/orders", body) or {}).get("orderId") or "")

    def _cancel(self, venue_id: str) -> None:
        self._call("DELETE", f"/orders/{venue_id}")

    def _orders(self) -> list[BookEntry]:
        body = self._call("GET", "/orders") or []
        return [
            BookEntry(
                str(o["orderId"]),
                STATUS.get(str(o.get("orderStatus", "")).upper(), "OPEN"),
                d(o.get("filledQty")),
                d(o.get("averageTradedPrice")),
                str(o.get("correlationId") or ""),
                str(o.get("omsErrorDescription") or ""),
                ist_time(o.get("exchangeTime") or o.get("updateTime")),
            )
            for o in (body if isinstance(body, list) else body.get("data") or [])
        ]


def _message(body: dict) -> str:
    return str(body.get("errorMessage") or body.get("remarks") or body.get("errorCode") or "")
