"""OANDA v20 REST adapter: spot gold (XAU/USD), silver and forex pairs.

Endpoints and field names follow OANDA's v20 API as published in its official `v20-python` SDK.
The practice environment (TESTNET here) trades virtual money with live prices; production trades a real
account. Connection credentials: the API token as the key and the account ID (e.g. 101-001-1234567-001)
as the secret.

Orders carry the platform's client order ID as their client extension, so they can be looked up with
the `@<id>` order specifier. Market orders are fill-or-kill. OANDA charges no commission on standard
accounts: the spread is in the fill price, and overnight financing is booked separately each day.
"""

from __future__ import annotations

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
from jdquant.marketdata.book import levels
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.marketdata.records import Candle, Quote
from jdquant.oms.orders import Order, OrderStatus, OrderType, ReportType, TimeInForce

BASE_URLS = {
    Environment.TESTNET: "https://api-fxpractice.oanda.com",  # practice account, virtual money
    Environment.PRODUCTION: "https://api-fxtrade.oanda.com",
}
GRANULARITY = {
    5: "S5",
    60: "M1",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D",
}
MAX_CANDLES = 5000  # per request
ASSET_CLASS = {"CURRENCY": AssetClass.FX, "METAL": AssetClass.COMMODITY, "CFD": AssetClass.INDEX}
TIF = {TimeInForce.GTC: "GTC", TimeInForce.IOC: "IOC", TimeInForce.FOK: "FOK", TimeInForce.DAY: "GTC"}
S = OrderStatus
WORKING = (S.SUBMITTED, S.UNKNOWN, S.OPEN, S.PARTIALLY_FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE)


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _ts(value: str) -> datetime:
    """Times are requested in UNIX format: seconds with up to nine decimals, as a string."""
    seconds, _, fraction = str(value).partition(".")
    return datetime.fromtimestamp(int(seconds), UTC) + timedelta(microseconds=int((fraction + "000000")[:6]))


class OandaAdapter(VenueAdapter):
    venue = "OANDA"

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
        if not (api_key and api_secret):
            raise VenueError(
                "CREDENTIALS_REQUIRED",
                "OANDA needs an API token and an account ID (e.g. 101-001-1234567-001)",
            )
        self._token = api_key
        self.account = api_secret.strip()
        self.environment = environment
        self._base = BASE_URLS[environment]
        self._limiter = TokenBucket(100, 1.0)  # OANDA allows 100 requests per second per connection
        self.currency = "USD"

    # ---- transport ----------------------------------------------------------------------------

    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None):
        self.guard()
        self._limiter.acquire()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept-Datetime-Format": "UNIX",
            "Content-Type": "application/json",
        }
        try:
            response = self._http.request(
                method, self._base + path, params=params, json=body, headers=headers
            )
        except httpx.TimeoutException as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        if response.status_code == 429:
            self.record_failure()
            raise VenueError("RATE_LIMITED", "OANDA rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"OANDA returned {response.status_code}")
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.status_code in (401, 403):
            message = data.get("errorMessage", "OANDA rejected the token")
            raise VenueError("API_KEY_INVALID", message, raw=data)
        if response.status_code == 404:
            raise VenueError("ORDER_NOT_FOUND", data.get("errorMessage", "not found"), raw=data)
        self.record_success()
        return response.status_code, data

    def _account_path(self, suffix: str = "") -> str:
        return f"/v3/accounts/{self.account}{suffix}"

    # ---- connection & reference data ----------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        status, data = self._request("GET", self._account_path("/summary"))
        if status != 200:
            raise VenueError("CONNECTION_FAILED", data.get("errorMessage", f"HTTP {status}"), raw=data)
        account = data["account"]
        self.currency = account.get("currency", "USD")
        practice = " (practice account, virtual money)" if self.environment is Environment.TESTNET else ""
        # API tokens can trade but never move money out of the account.
        return ConnectionTest(
            True, True, True, False, None, f"OANDA {self.currency} account{practice}", self._balances(account)
        )

    def _balances(self, account: dict) -> list[Balance]:
        available = _d(account.get("marginAvailable", account.get("balance", 0)))
        used = _d(account.get("marginUsed", 0))
        return [Balance(account.get("currency", self.currency), available, used)]

    def fetch_balances(self) -> list[Balance]:
        _, data = self._request("GET", self._account_path("/summary"))
        return self._balances(data["account"])

    def fetch_instruments(self) -> list[Instrument]:
        _, data = self._request("GET", self._account_path("/instruments"))
        instruments = []
        for item in data.get("instruments", []):
            name = item["name"]
            base, _, quote = name.partition("_")
            units_precision = int(item.get("tradeUnitsPrecision", 0))
            lot = Decimal(1).scaleb(-units_precision)
            maximum = item.get("maximumOrderUnits")
            instruments.append(
                Instrument(
                    venue=self.venue,
                    symbol=name,
                    asset_class=ASSET_CLASS.get(item.get("type", ""), AssetClass.FX),
                    base_asset=base,
                    quote_asset=quote,
                    tick_size=Decimal(1).scaleb(-int(item.get("displayPrecision", 5))),
                    lot_size=lot,
                    min_quantity=max(_d(item.get("minimumTradeSize", lot)), lot),
                    max_quantity=_d(maximum) if maximum else None,
                    aliases=(("OANDA", name),),
                    shortable=True,
                )
            )
        return instruments

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        _, data = self._request(
            "GET", self._account_path("/pricing"), params={"instruments": instrument.symbol}
        )
        prices = data.get("prices") or []
        if not prices or not prices[0].get("bids") or not prices[0].get("asks"):
            return None
        price = prices[0]
        bid, ask = price["bids"][0], price["asks"][0]
        return Quote(
            instrument.instrument_id,
            _ts(price["time"]),  # stays old while the market is closed, so the feed shows as stale
            _d(bid["price"]),
            _d(bid.get("liquidity", 0)),
            _d(ask["price"]),
            _d(ask.get("liquidity", 0)),
        )

    def fetch_depth(self, instrument: Instrument):
        """OANDA quotes several prices per side, each good for a stated liquidity (units)."""
        _, data = self._request(
            "GET", self._account_path("/pricing"), params={"instruments": instrument.symbol}
        )
        price = (data.get("prices") or [{}])[0]
        return self.book(
            instrument,
            bids=levels(price.get("bids"), bid=True, qty_key="liquidity"),
            asks=levels(price.get("asks"), bid=False, qty_key="liquidity"),
            at=_ts(price["time"]) if price.get("time") else None,
        )

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        granularity = GRANULARITY.get(interval_seconds)
        if granularity is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"OANDA has no {interval_seconds}s candles")
        candles: list[Candle] = []
        to: str | None = None
        while len(candles) < limit:
            count = min(MAX_CANDLES, limit - len(candles))
            params: dict[str, Any] = {"granularity": granularity, "price": "M", "count": count}
            if to is not None:
                params["to"] = to
            _, data = self._request("GET", f"/v3/instruments/{instrument.symbol}/candles", params=params)
            rows = data.get("candles") or []
            batch = [self._candle(instrument, interval_seconds, r) for r in rows if r.get("complete")]
            candles = batch + candles
            if len(rows) < count or not rows:
                break
            to = rows[0]["time"]  # page backwards from the oldest candle received
        return candles[-limit:]

    @staticmethod
    def _candle(instrument: Instrument, interval_seconds: int, row: dict) -> Candle:
        mid, opened = row["mid"], _ts(row["time"])
        return Candle(
            instrument.instrument_id,
            interval_seconds,
            opened,
            opened + timedelta(seconds=interval_seconds),
            _d(mid["o"]),
            _d(mid["h"]),
            _d(mid["l"]),
            _d(mid["c"]),
            _d(row.get("volume", 0)),  # tick volume: price changes, not traded size
        )

    # ---- trading ------------------------------------------------------------------------------

    def _order_body(self, order: Order) -> dict:
        symbol = order.instrument_id.split(":", 1)[1]
        units = order.quantity if order.side.sign > 0 else -order.quantity
        request: dict[str, Any] = {
            "instrument": symbol,
            "units": f"{units.normalize():f}",
            "positionFill": "DEFAULT",
            "clientExtensions": {"id": order.client_order_id, "tag": "jdquant"},
        }
        if order.order_type is OrderType.MARKET:
            request.update(type="MARKET", timeInForce="FOK")
        elif order.order_type is OrderType.LIMIT:
            request.update(
                type="LIMIT",
                price=f"{order.limit_price.normalize():f}",
                timeInForce=TIF.get(order.time_in_force, "GTC"),
            )
        else:
            raise VenueError("ORDER_TYPE_UNSUPPORTED", f"{order.order_type} orders are not supported")
        return {"order": request}

    def submit(self, order: Order) -> None:
        try:
            body = self._order_body(order)
            status, data = self._request("POST", self._account_path("/orders"), body=body)
        except VenueTimeout:
            return  # outcome unknown: the OMS resolves it by query
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        if status == 400 or "orderRejectTransaction" in data:
            reject = data.get("orderRejectTransaction") or {}
            reason = reject.get("rejectReason") or data.get("errorCode") or "ORDER_REJECTED"
            self.emit(ReportType.REJECT, order, reason=reason)
            return
        created = data.get("orderCreateTransaction") or {}
        self.emit(ReportType.ACK, order, venue_order_id=str(created.get("id", "")))
        if data.get("orderFillTransaction"):
            self._emit_fill(order, data["orderFillTransaction"])
        cancel = data.get("orderCancelTransaction")
        if cancel:
            self.emit(ReportType.EXPIRED, order, reason=cancel.get("reason", "CANCELLED"))

    def _emit_fill(self, order: Order, fill: dict) -> None:
        fee = _d(fill.get("commission", 0)) + _d(fill.get("guaranteedExecutionFee", 0))
        self.emit_fill(
            order,
            trade_id=str(fill["id"]),
            price=_d(fill["price"]),
            quantity=abs(_d(fill["units"])),
            fee=abs(fee),
            fee_asset=self.currency,
            is_maker=None,
            at=_ts(fill["time"]),
        )

    def cancel(self, order: Order) -> None:
        try:
            self._request("PUT", self._account_path(f"/orders/@{order.client_order_id}/cancel"))
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.CANCELED, order)

    def query(self, order: Order) -> None:
        try:
            _, data = self._request("GET", self._account_path(f"/orders/@{order.client_order_id}"))
        except VenueTimeout:
            return
        except VenueError as exc:
            if exc.code == "ORDER_NOT_FOUND":
                self.emit(ReportType.NOT_FOUND, order)
            return
        venue_order = data.get("order") or {}
        state = venue_order.get("state")
        if order.status in (S.SUBMITTED, S.UNKNOWN):
            self.emit(ReportType.ACK, order, venue_order_id=str(venue_order.get("id", "")))
        if state == "FILLED" and venue_order.get("fillingTransactionID"):
            tid = venue_order["fillingTransactionID"]
            _, tx = self._request("GET", self._account_path(f"/transactions/{tid}"))
            if tx.get("transaction"):
                self._emit_fill(order, tx["transaction"])  # the OMS ignores fills it already has
        elif state == "CANCELLED":
            self.emit(ReportType.CANCELED, order)

    def poll(self, orders: list[Order]) -> None:
        for order in orders:
            if order.status in WORKING:
                self.query(order)
