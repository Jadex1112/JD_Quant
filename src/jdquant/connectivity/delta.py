"""Delta Exchange India: crypto perpetual futures (BTC, ETH, ... settled in USD).

Endpoints, the request signature and payloads follow Delta's official `delta-rest-client`: each private
request carries the API key, a timestamp and an HMAC-SHA256 of method + timestamp + path + query + body.
The testnet (TESTNET) and production accounts are independent, with separate keys and product ids.
Order sizes are whole contracts; each contract is worth `contract_value` of the underlying coin.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any
from urllib.parse import quote_plus

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
from jdquant.marketdata.book import levels
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentStatus
from jdquant.marketdata.records import Candle, Quote
from jdquant.oms.orders import Order, OrderStatus, OrderType, ReportType, TimeInForce

BASE_URLS = {
    Environment.PRODUCTION: "https://api.india.delta.exchange",
    Environment.TESTNET: "https://cdn-ind.testnet.deltaex.org",
}
RESOLUTIONS = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    86400: "1d",
}
MAX_CANDLES = 2000
S = OrderStatus
LIVE = (S.SUBMITTED, S.UNKNOWN, S.OPEN, S.PARTIALLY_FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE)


def _d(value: Any) -> Decimal:
    return Decimal(str(value)).normalize() if value not in (None, "") else Decimal(0)


def client_id(client_order_id: str) -> str:
    """Delta allows 32 characters; the platform's id without its prefix fits."""
    return client_order_id.replace("-", "")[-32:]


class DeltaAdapter(VenueAdapter):
    venue = "DELTA"

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
        self._key, self._secret = api_key, api_secret
        self.environment = environment
        self._base = BASE_URLS[environment]
        self._limiter = TokenBucket(20, 1.0)
        self._products: dict[str, int] = {}  # symbol -> product id
        self._seen: dict[str, tuple[Decimal, Decimal]] = {}
        self.lookup = None  # the platform's instrument registry, attached by the connection manager

    def _fee(self, order: Order, quantity: Decimal, price: Decimal) -> Decimal:
        """Delta reports commission per fill only in the fills feed; estimate it with the fee model."""
        from jdquant.markets.india import CRYPTO_PERP_FEES
        from jdquant.oms.orders import Liquidity

        if self.lookup is None:
            return Decimal(0)
        try:
            instrument = self.lookup(order.instrument_id)
        except Exception:
            return Decimal(0)
        return CRYPTO_PERP_FEES.fee(instrument, order.side, quantity, price, Liquidity.TAKER).quantize(
            Decimal("0.00000001")
        )

    # ---- transport ----------------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict | None = None,
        body: dict | None = None,
        auth: bool = False,
    ) -> Any:
        self.guard()
        self._limiter.acquire()
        query_string = (
            ("?" + "&".join(f"{k}={quote_plus(str(v))}" for k, v in query.items())) if query else ""
        )
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "jdquant"}
        if auth:
            if not (self._key and self._secret):
                raise VenueError("CREDENTIALS_REQUIRED", "this needs a Delta API key and secret")
            timestamp = str(int(time.time()))
            message = method + timestamp + path + query_string + payload
            headers.update(
                {
                    "api-key": self._key,
                    "timestamp": timestamp,
                    "signature": hmac.new(
                        self._secret.encode(), message.encode(), hashlib.sha256
                    ).hexdigest(),
                }
            )
        try:
            response = self._http.request(
                method, self._base + path + query_string, content=payload or None, headers=headers
            )
        except httpx.TimeoutException as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        if response.status_code == 429:
            self.record_failure()
            raise VenueError("RATE_LIMITED", "Delta rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"Delta returned {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise VenueError("VENUE_ERROR", f"unexpected Delta response ({response.status_code})") from None
        if response.status_code >= 400 or not data.get("success", False):
            error = data.get("error") or {}
            code = str(error.get("code") or f"HTTP {response.status_code}")
            if response.status_code == 401 or code in (
                "InvalidApiKey",
                "UnauthorizedApiAccess",
                "Signature Mismatch",
            ):
                raise VenueError("API_KEY_INVALID", f"Delta: {code}", raw=data)
            if code in ("open_order_not_found", "order_not_found"):
                raise VenueError("ORDER_NOT_FOUND", code, raw=data)
            if "insufficient" in code:
                raise VenueError("INSUFFICIENT_BALANCE", code, raw=data)
            raise VenueError(
                "ORDER_REJECTED" if path.startswith("/v2/orders") else "VENUE_ERROR", code, raw=data
            )
        self.record_success()
        return data.get("result")

    # ---- account and reference data ---------------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        if not (self._key and self._secret):
            return ConnectionTest(True, False, False, None, None, "public market data only")
        balances = self.fetch_balances()
        testnet = " (testnet)" if self.environment is Environment.TESTNET else ""
        # Delta API keys are created with trading permission only; withdrawals need the web app.
        return ConnectionTest(True, True, True, False, None, f"Delta Exchange India{testnet}", balances)

    def fetch_balances(self) -> list[Balance]:
        rows = self._request("GET", "/v2/wallet/balances", auth=True) or []
        out = []
        for row in rows:
            total, available = _d(row.get("balance")), _d(row.get("available_balance"))
            if total:
                out.append(Balance(str(row.get("asset_symbol") or ""), available, total - available))
        return out

    def fetch_instruments(self) -> list[Instrument]:
        out = []
        for p in self._request("GET", "/v2/products") or []:
            if p.get("contract_type") != "perpetual_futures" or p.get("state") not in (None, "live"):
                continue
            symbol = str(p["symbol"])
            self._products[symbol] = int(p["id"])
            underlying = (p.get("underlying_asset") or {}).get("symbol") or symbol.removesuffix("USD")
            quote = (p.get("quoting_asset") or {}).get("symbol") or "USD"
            out.append(
                Instrument(
                    venue=self.venue,
                    symbol=symbol,
                    asset_class=AssetClass.CRYPTO_PERPETUAL,
                    base_asset=underlying,
                    quote_asset=quote,
                    tick_size=_d(p.get("tick_size") or "0.5"),
                    lot_size=Decimal(1),
                    min_quantity=Decimal(1),
                    contract_multiplier=_d(p.get("contract_value") or 1),
                    status=InstrumentStatus.ACTIVE,
                    aliases=((self.venue, f"{symbol}|{p['id']}"),),
                    shortable=True,
                )
            )
        return out

    def remember(self, instruments: list[Instrument]) -> None:
        for instrument in instruments:
            for source, ref in instrument.aliases:
                if source == self.venue:
                    symbol, _, product_id = ref.partition("|")
                    self._products[symbol] = int(product_id)

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        ticker = self._request("GET", f"/v2/tickers/{instrument.symbol}") or {}
        quotes = ticker.get("quotes") or {}
        bid, ask = _d(quotes.get("best_bid")), _d(quotes.get("best_ask"))
        if not bid or not ask or bid >= ask:
            return None
        stamp = ticker.get("timestamp")
        at = datetime.fromtimestamp(int(stamp) / 1_000_000, UTC) if stamp else self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, _d(quotes.get("bid_size")), ask, _d(quotes.get("ask_size"))
        )

    def fetch_depth(self, instrument: Instrument):
        """The L2 book in contracts (each contract is `contract_multiplier` of the coin)."""
        book = self._request("GET", f"/v2/l2orderbook/{instrument.symbol}") or {}
        stamp = book.get("last_updated_at")
        return self.book(
            instrument,
            bids=levels(book.get("buy"), bid=True, qty_key="size"),
            asks=levels(book.get("sell"), bid=False, qty_key="size"),
            at=datetime.fromtimestamp(int(stamp) / 1_000_000, UTC) if stamp else None,
            capacity=20,
        )

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        resolution = RESOLUTIONS.get(interval_seconds)
        if resolution is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"Delta has no {interval_seconds}s candles")
        now = self._clock.now()
        end = int(now.timestamp())
        rows: dict[int, dict] = {}
        while len(rows) < limit:
            start = end - interval_seconds * min(MAX_CANDLES, limit - len(rows) + 1)
            batch = (
                self._request(
                    "GET",
                    "/v2/history/candles",
                    query={"resolution": resolution, "symbol": instrument.symbol, "start": start, "end": end},
                )
                or []
            )
            for row in batch:
                rows[int(row["time"])] = row
            if len(batch) < 2:
                break
            end = start
        step = timedelta(seconds=interval_seconds)
        out = []
        for t in sorted(rows):
            opened = datetime.fromtimestamp(t, UTC)
            if opened + step > now:
                continue
            r = rows[t]
            out.append(
                Candle(
                    instrument.instrument_id,
                    interval_seconds,
                    opened,
                    opened + step,
                    _d(r["open"]),
                    _d(r["high"]),
                    _d(r["low"]),
                    _d(r["close"]),
                    _d(r.get("volume")),
                )
            )
        return out[-limit:]

    # ---- trading ----------------------------------------------------------------------------------------

    def _product_id(self, order: Order) -> int:
        symbol = order.instrument_id.split(":", 1)[1]
        if symbol not in self._products:
            raise VenueError("INSTRUMENT_UNSUPPORTED", f"unknown Delta product {symbol}")
        return self._products[symbol]

    def submit(self, order: Order) -> None:
        try:
            if order.quantity != order.quantity.to_integral_value(rounding=ROUND_FLOOR):
                raise VenueError("INVALID_QUANTITY", "Delta sizes are whole contracts")
            body: dict[str, Any] = {
                "product_id": self._product_id(order),
                "size": int(order.quantity),
                "side": "buy" if order.side is Side.BUY else "sell",
                "client_order_id": client_id(order.client_order_id),
                "reduce_only": "true" if order.reduce_only else "false",
            }
            if order.order_type is OrderType.MARKET:
                body["order_type"] = "market_order"
            elif order.order_type is OrderType.LIMIT:
                body.update(
                    order_type="limit_order",
                    limit_price=f"{order.limit_price.normalize():f}",
                    post_only="true" if order.post_only else "false",
                    time_in_force="ioc" if order.time_in_force is TimeInForce.IOC else "gtc",
                )
            else:
                raise VenueError("ORDER_TYPE_UNSUPPORTED", "market and limit orders are supported")
            result = self._request("POST", "/v2/orders", body=body, auth=True)
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.ACK, order, venue_order_id=str(result.get("id", "")))
        self._apply(order, result)

    def cancel(self, order: Order) -> None:
        try:
            self._request(
                "DELETE",
                "/v2/orders",
                body={"id": int(order.venue_order_id or 0), "product_id": self._product_id(order)},
                auth=True,
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.query(order)

    def query(self, order: Order) -> None:
        try:
            result = self._request(
                "GET", f"/v2/orders/client_order_id/{client_id(order.client_order_id)}", auth=True
            )
        except VenueTimeout:
            return
        except VenueError as exc:
            if exc.code == "ORDER_NOT_FOUND":
                self.emit(ReportType.NOT_FOUND, order)
            return
        if order.status in (S.SUBMITTED, S.UNKNOWN):
            self.emit(ReportType.ACK, order, venue_order_id=str(result.get("id", "")))
        self._apply(order, result)

    def poll(self, orders: list[Order]) -> None:
        for order in orders:
            if order.status in LIVE:
                self.query(order)

    def _apply(self, order: Order, result: dict) -> None:
        venue_id = str(result.get("id", ""))
        size, unfilled = _d(result.get("size")), _d(result.get("unfilled_size"))
        filled, avg = size - unfilled, _d(result.get("average_fill_price"))
        known = (order.filled_quantity, order.average_fill_price or Decimal(0))
        prev_filled, prev_avg = self._seen.get(venue_id, known)
        if filled > prev_filled and avg > 0:
            delta = filled - prev_filled
            price = (avg * filled - prev_avg * prev_filled) / delta
            self.emit_fill(
                order,
                trade_id=f"{venue_id}:{filled}",
                price=price.normalize(),
                quantity=delta,
                fee=self._fee(order, delta, price),
                fee_asset="USD",
                is_maker=None,
                at=self._clock.now(),
            )
        self._seen[venue_id] = (filled, avg)
        state = result.get("state")
        if state == "cancelled" and filled < size:
            self.emit(ReportType.CANCELED, order)
