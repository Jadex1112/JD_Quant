"""Any crypto exchange that CCXT supports (https://github.com/ccxt/ccxt, MIT licence).

CCXT gives one API over 100+ exchanges, including the Indian ones Bitbns, ZebPay and Mudrex. A
connection's venue is `CCXT_<EXCHANGE>` (for example `CCXT_BITBNS`) and its instruments are listed as
`<EXCHANGE>:<exchange's own market id>` (for example `BITBNS:BTCINR`), so ids never contain a slash.
Exchanges that have their own adapter here (Binance, Delta Exchange, Alpaca) are served by those.

What comes through: spot markets (and perpetual swaps, which can be shorted, when chosen), quotes, the
order book, candles, balances, market and limit orders, cancels and order status. Fills are derived from
each order's cumulative filled quantity and average price, which every CCXT exchange reports.

CCXT cannot tell whether API keys may withdraw funds, so create keys with trading permission only.
`ccxt` is an optional dependency: `pip install ccxt`.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from jdquant.connectivity.base import (
    Balance,
    ConnectionTest,
    Environment,
    VenueAdapter,
    VenueError,
    VenueTimeout,
)
from jdquant.core.clock import Clock
from jdquant.marketdata.book import levels
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentStatus
from jdquant.marketdata.records import Candle, Quote
from jdquant.oms.orders import Order, OrderStatus, OrderType, ReportType

PREFIX = "CCXT_"
NATIVE = {"binance": "BINANCE", "delta": "DELTA", "alpaca": "ALPACA"}  # use the platform's own adapters
INDIAN = ("bitbns", "zebpay", "mudrex")
TIMEFRAMES = {60: "1m", 180: "3m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 7200: "2h",
              14400: "4h", 21600: "6h", 43200: "12h", 86400: "1d", 604800: "1w"}  # fmt: skip
S = OrderStatus


def _ccxt():
    try:
        import ccxt
    except ImportError:  # pragma: no cover - depends on the installation
        raise VenueError("CCXT_NOT_INSTALLED", "install the ccxt package: pip install ccxt") from None
    return ccxt


def exchange_of(venue: str) -> str:
    """`CCXT_BITBNS` -> `bitbns`."""
    return venue[len(PREFIX) :].lower()


def exchanges() -> list[dict[str, Any]]:
    """The exchanges a connection can use, with what each supports."""
    ccxt = _ccxt()
    out = []
    for exchange_id in ccxt.exchanges:
        if exchange_id in NATIVE:
            continue
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            continue
        try:
            describe = cls().describe()
        except Exception:  # an exchange whose description fails is simply not offered
            continue
        has = describe.get("has") or {}
        out.append(
            {
                "id": exchange_id,
                "venue": PREFIX + exchange_id.upper(),
                "name": describe.get("name") or exchange_id,
                "countries": describe.get("countries") or [],
                "indian": exchange_id in INDIAN,
                "testnet": bool((describe.get("urls") or {}).get("test")),
                "candles": bool(has.get("fetchOHLCV")),
                "order_book": bool(has.get("fetchOrderBook")),
                "trading": bool(has.get("createOrder")),
                "swaps": bool(has.get("swap")),
                "needs_password": bool((describe.get("requiredCredentials") or {}).get("password")),
            }
        )
    return sorted(out, key=lambda e: (not e["indian"], e["name"].lower()))


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value)).normalize()


def _market_id(market: dict) -> str:
    return str(market.get("id") or market["symbol"]).replace("/", "").replace(":", "-").upper()


class CcxtAdapter(VenueAdapter):
    venue = "CCXT"
    uses_settings = True

    def __init__(
        self,
        clock: Clock,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        environment: Environment = Environment.PRODUCTION,
        http=None,
        settings: dict[str, Any] | None = None,
        exchange_id: str | None = None,
        client: Any = None,
    ):
        super().__init__(clock, http=http)
        settings = settings or {}
        exchange_id = (exchange_id or settings.get("exchange") or "").lower()
        if exchange_id in NATIVE:
            raise VenueError(
                "USE_NATIVE_ADAPTER", f"connect {NATIVE[exchange_id]} directly; it has its own adapter here"
            )
        self.exchange_id = exchange_id
        self.venue = exchange_id.upper()
        self.markets = (self.venue,)
        self.environment = environment
        self._types = (
            {"spot", "swap"}
            if settings.get("market_type") == "both"
            else {settings.get("market_type") or "spot"}
        )
        self._quotes = {q.upper() for q in settings.get("quotes") or []}
        self._lock = threading.RLock()
        self._symbols: dict[str, str] = {}  # instrument id -> CCXT unified symbol
        self._has_keys = bool(api_key)
        self._fees: dict[str, Decimal] = {}  # client order id -> fee already booked
        if client is None:
            ccxt = _ccxt()
            cls = getattr(ccxt, exchange_id, None)
            if cls is None or exchange_id not in ccxt.exchanges:
                raise VenueError("EXCHANGE_UNKNOWN", f"CCXT has no exchange {exchange_id!r}")
            config: dict[str, Any] = {"enableRateLimit": True, "timeout": 15000}
            if api_key:
                config["apiKey"] = api_key
                secret = api_secret or ""
                if secret.startswith("{"):  # exchanges that also need a passphrase or uid
                    extra = json.loads(secret)
                    config.update({k: v for k, v in extra.items() if k in ("secret", "password", "uid")})
                else:
                    config["secret"] = secret
            client = cls(config)
            if environment is Environment.TESTNET:
                try:
                    client.set_sandbox_mode(True)
                except Exception:
                    raise VenueError(
                        "TESTNET_UNAVAILABLE",
                        f"{client.name} has no test environment in CCXT; choose production",
                    ) from None
        self.client = client

    # ---- plumbing -------------------------------------------------------------------------------

    def _call(self, method: str, *args, **kwargs) -> Any:
        ccxt = _ccxt()
        self.guard()
        try:
            with self._lock:
                result = getattr(self.client, method)(*args, **kwargs)
        except ccxt.AuthenticationError as exc:
            raise VenueError("API_KEY_INVALID", str(exc)[:300]) from None
        except ccxt.InsufficientFunds as exc:
            raise VenueError("INSUFFICIENT_BALANCE", str(exc)[:300]) from None
        except ccxt.OrderNotFound as exc:
            raise VenueError("ORDER_NOT_FOUND", str(exc)[:300]) from None
        except ccxt.BadSymbol as exc:
            raise VenueError("UNKNOWN_INSTRUMENT", str(exc)[:300]) from None
        except ccxt.InvalidOrder as exc:
            raise VenueError("ORDER_REJECTED", str(exc)[:300]) from None
        except ccxt.NotSupported as exc:
            raise VenueError("NOT_SUPPORTED", str(exc)[:300]) from None
        except ccxt.RateLimitExceeded as exc:
            self.record_failure()
            raise VenueError("RATE_LIMITED", str(exc)[:300], retryable=True) from None
        except ccxt.RequestTimeout as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)[:300]) from None
        except ccxt.NetworkError as exc:  # includes ExchangeNotAvailable
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc)[:300], retryable=True) from None
        except ccxt.ExchangeError as exc:
            raise VenueError("VENUE_ERROR", str(exc)[:300]) from None
        self.record_success()
        return result

    def _symbol(self, instrument_id: str) -> str:
        symbol = self._symbols.get(instrument_id)
        if symbol is None:
            raise VenueError("UNKNOWN_INSTRUMENT", f"{instrument_id} is not loaded; test the connection")
        return symbol

    def remember(self, instruments: list[Instrument]) -> None:
        """Restore the id -> CCXT symbol map from stored instruments after a restart."""
        for instrument in instruments:
            for source, symbol in instrument.aliases:
                if source == "CCXT":
                    self._symbols[instrument.instrument_id] = symbol

    def _tick(self, value: Any) -> Decimal | None:
        """A precision figure as a step size, whatever CCXT precision mode the exchange uses."""
        if value is None:
            return None
        ccxt = _ccxt()
        mode = getattr(self.client, "precisionMode", ccxt.TICK_SIZE)
        if mode == ccxt.DECIMAL_PLACES:
            return Decimal(1).scaleb(-int(value))
        if mode == ccxt.SIGNIFICANT_DIGITS:
            return None  # a relative precision has no fixed step
        return _d(value)

    # ---- connection & reference data --------------------------------------------------------------

    def test_connection(self) -> ConnectionTest:
        self._call("load_markets")
        if not self._has_keys:
            return ConnectionTest(
                True, False, False, None, None, f"{self.client.name}: public market data only"
            )
        balances = self.fetch_balances()
        return ConnectionTest(
            True, True, bool(self.client.has.get("createOrder")), None, None,
            f"{self.client.name}: keys accepted (make sure they cannot withdraw funds)", balances,
        )  # fmt: skip

    def fetch_instruments(self) -> list[Instrument]:
        markets = self._call("load_markets")
        out = []
        for market in markets.values():
            kind = market.get("type") or ("spot" if market.get("spot") else None)
            if kind not in self._types or market.get("active") is False:
                continue
            if kind == "swap" and not market.get("linear", True):
                continue  # inverse swaps settle in the coin; not supported
            if self._quotes and str(market.get("quote", "")).upper() not in self._quotes:
                continue
            precision, limits = market.get("precision") or {}, market.get("limits") or {}
            tick = self._tick(precision.get("price")) or Decimal("0.00000001")
            lot = self._tick(precision.get("amount")) or Decimal("0.00000001")
            amount, cost = limits.get("amount") or {}, limits.get("cost") or {}
            instrument = Instrument(
                venue=self.venue,
                symbol=_market_id(market),
                asset_class=AssetClass.CRYPTO_PERPETUAL if kind == "swap" else AssetClass.CRYPTO_SPOT,
                base_asset=str(market.get("base", "")).upper(),
                quote_asset=str(market.get("quote", "")).upper(),
                tick_size=tick,
                lot_size=lot,
                min_quantity=_d(amount.get("min")) or lot,
                max_quantity=_d(amount.get("max")),
                min_notional=_d(cost.get("min")),
                contract_multiplier=_d(market.get("contractSize")) or Decimal(1)
                if kind == "swap"
                else Decimal(1),
                status=InstrumentStatus.ACTIVE,
                aliases=(("CCXT", market["symbol"]),),
                shortable=kind == "swap",
            )
            self._symbols[instrument.instrument_id] = market["symbol"]
            out.append(instrument)
        return out

    def fetch_quote(self, instrument: Instrument) -> Quote | None:
        t = self._call("fetch_ticker", self._symbol(instrument.instrument_id))
        bid, ask = _d(t.get("bid")), _d(t.get("ask"))
        if bid is None or ask is None:
            last = _d(t.get("last"))
            if last is None:
                return None
            bid = ask = last
        at = datetime.fromtimestamp(t["timestamp"] / 1000, UTC) if t.get("timestamp") else self._clock.now()
        return Quote(
            instrument.instrument_id, at, bid, _d(t.get("bidVolume")) or Decimal(0), ask,
            _d(t.get("askVolume")) or Decimal(0),
        )  # fmt: skip

    def fetch_depth(self, instrument: Instrument):
        if not self.client.has.get("fetchOrderBook"):
            return None
        book = self._call("fetch_order_book", self._symbol(instrument.instrument_id), 20)

        def rows(side):
            return [{"price": p, "quantity": q} for p, q, *_ in book.get(side) or []]

        at = datetime.fromtimestamp(book["timestamp"] / 1000, UTC) if book.get("timestamp") else None
        return self.book(
            instrument,
            bids=levels(rows("bids"), bid=True),
            asks=levels(rows("asks"), bid=False),
            at=at,
            capacity=20,
        )

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        timeframe = TIMEFRAMES.get(interval_seconds)
        offered = getattr(self.client, "timeframes", None) or {}
        if (
            not self.client.has.get("fetchOHLCV")
            or timeframe is None
            or (offered and timeframe not in offered)
        ):
            raise VenueError("INTERVAL_UNSUPPORTED", f"{self.client.name} has no {interval_seconds}s candles")
        rows = self._call(
            "fetch_ohlcv", self._symbol(instrument.instrument_id), timeframe, None, min(limit, 1000)
        )
        now_ms = self._clock.now().timestamp() * 1000
        out = []
        for ts, o, h, low, c, v in rows:
            if ts + interval_seconds * 1000 > now_ms:
                continue  # the bar is still forming
            opened = datetime.fromtimestamp(ts / 1000, UTC)
            closed = datetime.fromtimestamp((ts + interval_seconds * 1000) / 1000, UTC)
            out.append(
                Candle(
                    instrument.instrument_id,
                    interval_seconds,
                    opened,
                    closed,
                    _d(o),
                    _d(h),
                    _d(low),
                    _d(c),
                    _d(v) or Decimal(0),
                )  # fmt: skip
            )
        return out

    def fetch_balances(self) -> list[Balance]:
        raw = self._call("fetch_balance")
        free, used = raw.get("free") or {}, raw.get("used") or {}
        out = []
        for asset in sorted(set(free) | set(used)):
            f, u = _d(free.get(asset)) or Decimal(0), _d(used.get(asset)) or Decimal(0)
            if f or u:
                out.append(Balance(asset.upper(), f, u))
        return out

    def fetch_positions(self) -> dict[str, Decimal] | None:
        by_symbol = {v: k for k, v in self._symbols.items()}
        out: dict[str, Decimal] = {}
        if "swap" in self._types and self.client.has.get("fetchPositions"):
            for p in self._call("fetch_positions") or []:
                iid = by_symbol.get(p.get("symbol"))
                if iid is None:
                    continue
                size = _d(p.get("contracts")) or Decimal(0)
                out[iid] = -size if p.get("side") == "short" else size
        if "spot" in self._types:
            held = {b.asset: b.total for b in self.fetch_balances()}
            for iid, symbol in self._symbols.items():
                base = symbol.split("/")[0].upper()
                if ":" not in symbol and held.get(base):
                    out.setdefault(iid, held[base])
        return out

    # ---- trading ----------------------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        if order.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            self.emit(ReportType.REJECT, order, reason="ORDER_TYPE_UNSUPPORTED")
            return
        params = {"clientOrderId": order.client_order_id}
        if order.post_only:
            params["postOnly"] = True
        try:
            placed = self._call(
                "create_order",
                self._symbol(order.instrument_id),
                "market" if order.order_type is OrderType.MARKET else "limit",
                order.side.value.lower(),
                float(order.quantity),
                float(order.limit_price) if order.limit_price is not None else None,
                params,
            )
        except VenueTimeout:
            return  # outcome unknown: the OMS resolves it by query
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.ACK, order, venue_order_id=str(placed["id"]))
        self._apply(order, placed)

    def cancel(self, order: Order) -> None:
        if not order.venue_order_id:
            self.emit(ReportType.CANCEL_REJECT, order, reason="ORDER_NOT_FOUND")
            return
        try:
            self._call("cancel_order", order.venue_order_id, self._symbol(order.instrument_id))
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.emit(ReportType.CANCELED, order)

    def query(self, order: Order) -> None:
        if not order.venue_order_id:
            return
        try:
            status = self._call("fetch_order", order.venue_order_id, self._symbol(order.instrument_id))
        except VenueTimeout:
            return
        except VenueError as exc:
            if exc.code == "ORDER_NOT_FOUND":
                self.emit(ReportType.NOT_FOUND, order)
            return
        if order.status in (S.SUBMITTED, S.UNKNOWN):
            self.emit(ReportType.ACK, order, venue_order_id=str(status["id"]))
        self._apply(order, status)

    def poll(self, orders: list[Order]) -> None:
        for order in orders:
            if order.status in (S.SUBMITTED, S.UNKNOWN, S.OPEN, S.PARTIALLY_FILLED, S.PENDING_CANCEL):
                self.query(order)

    def _apply(self, order: Order, report: dict) -> None:
        """Turn the cumulative fill into fills: the new quantity at the price that explains the average."""
        filled = _d(report.get("filled")) or Decimal(0)
        average = _d(report.get("average")) or _d(report.get("price"))
        done = order.filled_quantity
        if filled > done and average:
            previous = order.average_fill_price or Decimal(0)
            price = (average * filled - previous * done) / (filled - done)
            fee = report.get("fee") or {}
            total_fee = _d(fee.get("cost")) or Decimal(0)
            new_fee = max(Decimal(0), total_fee - self._fees.get(order.client_order_id, Decimal(0)))
            self._fees[order.client_order_id] = max(
                total_fee, self._fees.get(order.client_order_id, Decimal(0))
            )
            self.emit_fill(
                order,
                trade_id=f"{report['id']}-{filled}",
                price=price,
                quantity=filled - done,
                fee=new_fee,
                fee_asset=str(fee.get("currency") or ""),
                is_maker=None,
                at=datetime.fromtimestamp(report["timestamp"] / 1000, UTC)
                if report.get("timestamp")
                else self._clock.now(),
            )
        status = report.get("status")
        if status == "canceled":
            self.emit(ReportType.CANCELED, order)
        elif status in ("expired", "rejected"):
            self.emit(
                ReportType.EXPIRED if status == "expired" else ReportType.REJECT, order, reason=status.upper()
            )
