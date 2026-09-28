"""Shared machinery for Indian brokers trading NSE stocks and ETFs (Zerodha, Upstox, Angel One, Dhan).

Every broker names the same share differently (Kite "SBIN" + token, Upstox "NSE_EQ|INE062A01020",
Angel One "SBIN-EQ" + token, Dhan security id "3045"). Instruments are registered under the platform's
one canonical id, NSE:<SYMBOL>-EQ, so a stock is the same instrument whichever broker loaded it; each
adapter keeps its own reference for it (stored as the instrument's alias for that broker).

Subclasses implement the broker's calls; this base turns order books into execution reports: cumulative
filled quantity and average price become individual fills, orders are tagged with the platform's order
id so an order whose submission timed out can still be found, and charges are estimated with the Indian
equity cost model because these brokers report no per-fill charges.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

import httpx

from jdquant.connectivity.base import VenueAdapter, VenueError, VenueTimeout
from jdquant.core.clock import Clock
from jdquant.core.errors import PlatformError
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentStatus
from jdquant.marketdata.records import Candle
from jdquant.markets.india import IST, IndiaEquityFees, Product, fees_for
from jdquant.oms.orders import Liquidity, Order, OrderStatus, OrderType, ReportType, TimeInForce

log = logging.getLogger(__name__)
S = OrderStatus
LIVE = (S.SUBMITTED, S.UNKNOWN, S.OPEN, S.PARTIALLY_FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE)
TIFS = {TimeInForce.DAY: "DAY", TimeInForce.GTC: "DAY", TimeInForce.IOC: "IOC"}


@dataclass
class BookEntry:
    """A broker's view of one order, normalised."""

    venue_id: str
    status: str  # PENDING | OPEN | FILLED | CANCELLED | REJECTED | EXPIRED
    filled: Decimal = Decimal(0)
    average_price: Decimal = Decimal(0)
    tag: str = ""
    message: str = ""
    at: datetime | None = None


def d(value: Any) -> Decimal:
    return Decimal(str(value)).normalize() if value not in (None, "") else Decimal(0)


def tag_for(client_order_id: str, length: int = 20) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", client_order_id)[-length:]


def tick_in_rupees(value: Any) -> Decimal:
    """Scrip masters give ticks in rupees or in paise; NSE equity ticks are never a rupee or more."""
    tick = d(value) or Decimal("0.05")
    return tick / 100 if tick >= 1 else tick


def ist_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)).astimezone(UTC)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST).astimezone(UTC)
        except ValueError:
            continue
    return None


def error_code(message: str) -> str:
    lowered = message.lower()
    if "fund" in lowered or "margin" in lowered or "insufficient" in lowered:
        return "INSUFFICIENT_BALANCE"
    if "qty" in lowered or "quantity" in lowered or "lot" in lowered:
        return "INVALID_QUANTITY"
    if "price" in lowered or "circuit" in lowered or "tick" in lowered:
        return "PRICE_OUT_OF_BAND"
    if "market" in lowered and ("closed" in lowered or "not open" in lowered):
        return "MARKET_CLOSED"
    if "symbol" in lowered or "instrument" in lowered or "scrip" in lowered:
        return "INSTRUMENT_UNSUPPORTED"
    return "ORDER_REJECTED"


def nse_equity(symbol: str, *, name: str, tick: Decimal, lot: Decimal, source: str, ref: str) -> Instrument:
    """The canonical NSE:<SYMBOL>-EQ instrument, with this broker's reference as an alias."""
    base = symbol.removesuffix("-EQ")
    upper = name.upper()
    etf = "ETF" in upper or base.endswith("BEES") or "BEES" in upper
    return Instrument(
        venue="NSE",
        symbol=f"{base}-EQ",
        asset_class=AssetClass.ETF if etf else AssetClass.EQUITY,
        base_asset=base,
        quote_asset="INR",
        tick_size=tick,
        lot_size=lot or Decimal(1),
        min_quantity=lot or Decimal(1),
        status=InstrumentStatus.ACTIVE,
        aliases=((source, ref),),
    )


class IndianCashBroker(VenueAdapter):
    """NSE cash equities through an Indian broker's REST API."""

    markets = ("NSE",)
    uses_product = True  # the connection chooses delivery (CNC) or intraday
    history_chunk_days: dict[int, int] = {}  # max days of candles per request by interval
    intervals: dict[int, Any] = {}

    def __init__(self, clock: Clock, *, http: httpx.Client | None = None, product: Product = Product.CNC):
        super().__init__(clock, http=http)
        self.product = product
        self.fees = IndiaEquityFees(product=product)
        self.lookup: Callable[[str], Instrument] | None = None
        self.on_session_changed: Callable[[dict[str, str | None]], None] | None = None
        self._refs: dict[str, str] = {}  # canonical instrument id -> broker reference
        self._venue_ids: dict[str, str] = {}
        self._seen: dict[str, tuple[Decimal, Decimal]] = {}
        self._book: dict[str, BookEntry] = {}

    # ---- references --------------------------------------------------------------------------

    def remember(self, instruments: list[Instrument]) -> None:
        """Learn this broker's references from instruments (after a sync or a restart)."""
        for instrument in instruments:
            for source, ref in instrument.aliases:
                if source == self.venue:
                    self._refs[instrument.instrument_id] = ref

    def has_ref(self, instrument: Instrument) -> bool:
        return instrument.instrument_id in self._refs

    def ref(self, instrument_id: str) -> str:
        ref = self._refs.get(instrument_id)
        if ref is None:
            raise VenueError("INSTRUMENT_UNSUPPORTED", f"{self.venue} has no reference for {instrument_id}")
        return ref

    def fetch_instruments(self) -> list[Instrument]:
        instruments = self._instruments()
        self.remember(instruments)
        return instruments

    # ---- broker calls (implemented by each adapter) ---------------------------------------------

    def _instruments(self) -> list[Instrument]:
        raise NotImplementedError

    def _history(
        self, instrument: Instrument, ref: str, interval: Any, start: datetime, end: datetime
    ) -> list:
        """Rows of (open time, open, high, low, close, volume) between start and end."""
        raise NotImplementedError

    def _place(self, order: Order, ref: str, tag: str) -> str:
        raise NotImplementedError

    def _cancel(self, venue_id: str) -> None:
        raise NotImplementedError

    def _orders(self) -> list[BookEntry]:
        raise NotImplementedError

    # ---- market data ------------------------------------------------------------------------------

    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]:
        interval = self.intervals.get(interval_seconds)
        if interval is None:
            raise VenueError("INTERVAL_UNSUPPORTED", f"{self.venue} has no {interval_seconds}s candles")
        ref = self.ref(instrument.instrument_id)
        daily = interval_seconds >= 86400
        bars_per_day = 1 if daily else max(1, 22_500 // interval_seconds)
        days_needed = int(limit / bars_per_day * 7 / 5) + 10
        chunk = timedelta(days=self.history_chunk_days.get(interval_seconds, 30))
        now = self._clock.now()
        start = now - timedelta(days=days_needed)
        rows: dict[datetime, tuple] = {}
        while start < now:
            end = min(start + chunk, now)
            for row in self._history(instrument, ref, interval, start, end):
                rows[row[0]] = row
            start = end
        step = timedelta(seconds=interval_seconds)
        candles = []
        for opened in sorted(rows):
            _, o, h, low, c, v = rows[opened][:6]
            if opened + step > now:
                continue  # still forming
            candles.append(
                Candle(
                    instrument.instrument_id,
                    interval_seconds,
                    opened,
                    opened + step,
                    d(o),
                    d(h),
                    d(low),
                    d(c),
                    d(v or 0),
                )
            )
        return candles[-limit:]

    # ---- trading ------------------------------------------------------------------------------------

    def _check(self, order: Order) -> str:
        if not order.instrument_id.startswith("NSE:") or not order.instrument_id.endswith("-EQ"):
            raise VenueError("INSTRUMENT_UNSUPPORTED", f"{self.venue} is connected for NSE stocks and ETFs")
        if (
            order.post_only
            or order.time_in_force not in TIFS
            or order.order_type
            not in (
                OrderType.MARKET,
                OrderType.LIMIT,
            )
        ):
            raise VenueError("ORDER_TYPE_UNSUPPORTED", "market and limit orders, day or IOC, are supported")
        if order.quantity != order.quantity.to_integral_value():
            raise VenueError("INVALID_QUANTITY", "NSE equity quantities are whole shares")
        return self.ref(order.instrument_id)

    def submit(self, order: Order) -> None:
        try:
            ref = self._check(order)
            venue_id = self._place(order, ref, tag_for(order.client_order_id))
        except VenueTimeout:
            return  # the OMS marks it UNKNOWN; the order book (found by tag) resolves it
        except VenueError as exc:
            self.emit(ReportType.REJECT, order, reason=exc.code)
            return
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
            self._cancel(venue_id)
        except VenueTimeout:
            return
        except VenueError as exc:
            self.emit(ReportType.CANCEL_REJECT, order, reason=exc.code)
            return
        self.query(order)

    def query(self, order: Order) -> None:
        try:
            self._load_book()
        except (VenueTimeout, VenueError):
            return
        self._reconcile(order)

    def poll(self, orders: list[Order]) -> None:
        working = [o for o in orders if o.status in LIVE]
        if not working:
            return
        try:
            self._load_book()  # one order-book call covers every working order
        except (VenueTimeout, VenueError):
            return
        for order in working:
            self._reconcile(order)

    def _load_book(self) -> None:
        self._book = {e.venue_id: e for e in self._orders()}

    def _venue_id(self, order: Order) -> str | None:
        venue_id = order.venue_order_id or self._venue_ids.get(order.client_order_id)
        if venue_id is None:  # e.g. a timed-out submission: find it by its tag
            tag = tag_for(order.client_order_id)
            venue_id = next((i for i, e in self._book.items() if e.tag and tag.endswith(e.tag)), None)
        return venue_id

    def _reconcile(self, order: Order) -> None:
        venue_id = self._venue_id(order)
        entry = self._book.get(venue_id) if venue_id else None
        if entry is None:
            if order.status in (S.SUBMITTED, S.UNKNOWN):
                self.emit(ReportType.NOT_FOUND, order)
            return
        self._venue_ids[order.client_order_id] = venue_id
        if entry.status == "REJECTED":
            self.emit(ReportType.REJECT, order, reason=error_code(entry.message))
            return
        if order.status in (S.SUBMITTED, S.UNKNOWN) and entry.status != "PENDING":
            self.emit(ReportType.ACK, order, venue_order_id=venue_id)
        known = (order.filled_quantity, order.average_fill_price or Decimal(0))
        prev_filled, prev_avg = self._seen.get(venue_id, known)
        if entry.filled > prev_filled and entry.average_price > 0:
            delta = entry.filled - prev_filled
            price = (entry.average_price * entry.filled - prev_avg * prev_filled) / delta
            self.emit_fill(
                order,
                trade_id=f"{venue_id}:{entry.filled}",
                price=price.quantize(Decimal("0.0001"), ROUND_FLOOR).normalize(),
                quantity=delta,
                fee=self._estimate_fee(order, delta, price),
                fee_asset="INR",
                is_maker=None,
                at=entry.at or self._clock.now(),
            )
        self._seen[venue_id] = (entry.filled, entry.average_price)
        if entry.status == "CANCELLED":
            self.emit(ReportType.CANCELED, order)
        elif entry.status == "EXPIRED":
            self.emit(ReportType.EXPIRED, order, reason="expired")

    def _estimate_fee(self, order: Order, quantity: Decimal, price: Decimal) -> Decimal:
        instrument = None
        if self.lookup is not None:
            try:
                instrument = self.lookup(order.instrument_id)
            except PlatformError:
                instrument = None
        if instrument is None:
            return self.fees.breakdown(order.side, quantity * price)["total"]
        return fees_for(instrument, self.product).fee(
            instrument, order.side, quantity, price, Liquidity.UNKNOWN
        )

    # ---- transport ------------------------------------------------------------------------------------

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        self.guard()
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            self.record_failure()
            raise VenueTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            self.record_failure()
            raise VenueError("VENUE_UNAVAILABLE", str(exc), retryable=True) from exc
        if response.status_code == 429:
            self.record_failure()
            raise VenueError("RATE_LIMITED", f"{self.venue} rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            self.record_failure()
            raise VenueTimeout(f"{self.venue} returned {response.status_code}")
        self.record_success()
        return response


def day_stats(last, open_, high, low, prev_close, volume, change=None) -> dict[str, Any] | None:
    """Normalize a broker's day statistics; the previous close falls back to last minus change."""

    def f(v):
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None

    last_f = f(last)
    if not last_f:
        return None
    prev = f(prev_close)
    if not prev and f(change) is not None:
        prev = last_f - f(change)
    return {
        "last": last_f,
        "open": f(open_),
        "high": f(high),
        "low": f(low),
        "prev_close": prev or None,
        "volume": f(volume),
    }


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def since_epoch(at: datetime) -> int:
    return int(at.astimezone(UTC).timestamp())
