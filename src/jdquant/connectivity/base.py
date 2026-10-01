"""Standard venue adapter interface (Chapter 45.2, CON-006) and shared HTTP plumbing."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

from jdquant.core.clock import Clock
from jdquant.core.errors import PlatformError
from jdquant.marketdata.book import BookSnapshot
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Candle, Quote
from jdquant.oms.orders import ExecutionReport, Liquidity, Order, ReportType

log = logging.getLogger(__name__)


class Environment(StrEnum):
    PRODUCTION = "PRODUCTION"
    TESTNET = "TESTNET"


class VenueError(PlatformError):
    """Normalized venue failure (FR-22005)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, raw: Any = None):
        super().__init__(code, message, details={"raw": raw} if raw is not None else None)
        self.retryable = retryable


class VenueTimeout(Exception):
    """The request may or may not have reached the venue."""


@dataclass
class Balance:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass
class ConnectionTest:
    ok: bool
    authenticated: bool
    can_trade: bool
    can_withdraw: bool | None
    clock_offset_ms: float | None
    message: str = ""
    balances: list[Balance] = field(default_factory=list)


class TokenBucket:
    """Request pacing below the venue limit (CON-041, FR-22020)."""

    def __init__(self, capacity: int, per_seconds: float, safety_margin: float = 0.1):
        self.capacity = max(1, int(capacity * (1 - safety_margin)))
        self.refill = self.capacity / per_seconds
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1, max_wait: float = 2.0) -> None:
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.refill)
                self._updated = now
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                wait = (weight - self._tokens) / self.refill
            if time.monotonic() + wait > deadline:
                raise VenueError("RATE_LIMITED", "local rate limit reached", retryable=True)
            time.sleep(wait)


class VenueAdapter(ABC):
    """Execution router plus market data and account access for one venue connection."""

    venue: str
    supports_replace: bool = False
    markets: tuple[str, ...] = ()  # instrument venues this adapter trades when they differ from `venue`
    requires_login: bool = False  # interactive broker login (OAuth) instead of static API keys

    def __init__(self, clock: Clock, *, http: httpx.Client | None = None, timeout: float = 10.0):
        self._clock = clock
        self._http = http or httpx.Client(timeout=timeout)
        self._handler: Callable[[ExecutionReport], None] | None = None
        self.circuit_failures = 0
        self.circuit_open_until = 0.0

    def set_report_handler(self, handler: Callable[[ExecutionReport], None]) -> None:
        self._handler = handler

    def emit(self, report_type: ReportType, order: Order, **fields: Any) -> None:
        if self._handler is None:
            raise RuntimeError("adapter has no report handler")
        self._handler(
            ExecutionReport(
                report_type=report_type,
                venue=self.venue,
                client_order_id=fields.pop("client_order_id", order.client_order_id),
                exchange_ts=fields.pop("exchange_ts", self._clock.now()),
                **fields,
            )
        )

    def emit_fill(
        self,
        order: Order,
        *,
        trade_id: str,
        price: Decimal,
        quantity: Decimal,
        fee: Decimal,
        fee_asset: str,
        is_maker: bool | None,
        at: datetime,
    ) -> None:
        liquidity = (
            Liquidity.UNKNOWN if is_maker is None else (Liquidity.MAKER if is_maker else Liquidity.TAKER)
        )
        self.emit(
            ReportType.FILL,
            order,
            price=price,
            quantity=quantity,
            fee=fee,
            fee_asset=fee_asset,
            liquidity=liquidity,
            venue_trade_id=trade_id,
            exchange_ts=at,
        )

    # ---- circuit breaker (FR-22026) -------------------------------------------------------------

    def guard(self) -> None:
        if time.monotonic() < self.circuit_open_until:
            raise VenueError("VENUE_UNAVAILABLE", f"{self.venue} is temporarily unavailable", retryable=True)

    def record_success(self) -> None:
        self.circuit_failures = 0

    def record_failure(self, threshold: int = 5, cooldown: float = 30.0) -> None:
        self.circuit_failures += 1
        if self.circuit_failures >= threshold:
            self.circuit_open_until = time.monotonic() + cooldown

    # ---- interface --------------------------------------------------------------------------

    @abstractmethod
    def test_connection(self) -> ConnectionTest: ...

    @abstractmethod
    def fetch_instruments(self) -> list[Instrument]: ...

    @abstractmethod
    def fetch_quote(self, instrument: Instrument) -> Quote | None: ...

    @abstractmethod
    def fetch_candles(self, instrument: Instrument, interval_seconds: int, limit: int) -> list[Candle]: ...

    @abstractmethod
    def fetch_balances(self) -> list[Balance]: ...

    @abstractmethod
    def submit(self, order: Order) -> None: ...

    @abstractmethod
    def cancel(self, order: Order) -> None: ...

    @abstractmethod
    def query(self, order: Order) -> None: ...

    @abstractmethod
    def poll(self, orders: list[Order]) -> None:
        """Emit reports for any change in the given working orders (fills, cancels, expiries)."""

    def is_ready(self) -> bool:
        """False while the adapter cannot make authenticated calls (e.g. awaiting a broker login)."""
        return True

    def fetch_depth(self, instrument: Instrument) -> BookSnapshot | None:
        """The visible order book over REST, where the venue publishes one (None otherwise)."""
        return None

    def fetch_positions(self) -> dict[str, Decimal] | None:
        """Net quantity held at the broker per instrument (positions plus holdings); None when unsupported."""
        return None

    def option_underlyings(self) -> tuple[str, ...]:
        """Underlyings whose option chains this venue can supply (e.g. NIFTY); empty when none."""
        return ()

    def fetch_option_chain(self, underlying: str, expiry=None):
        """The `intelligence.options.OptionChain` for an underlying, nearest expiry by default."""
        return None

    def fetch_snapshots(self, instruments: list[Instrument]) -> dict[str, dict[str, Any]]:
        """Day statistics for many instruments at once: last, open, high, low, previous close, volume.

        The default asks for one quote at a time (so only a few instruments); brokers with a batch
        quote endpoint override it.
        """
        out: dict[str, dict[str, Any]] = {}
        for instrument in instruments[:20]:
            quote = self.fetch_quote(instrument)
            if quote is not None:
                out[instrument.instrument_id] = {"last": float(quote.mid)}
        return out

    def book(self, instrument: Instrument, *, bids, asks, at: datetime | None, capacity: int = 5, **fields):
        """A normalized snapshot stamped with this venue as its source; numbers may be str/Decimal/None."""
        now = self._clock.now()
        values = {}
        for key, value in fields.items():
            try:
                values[key] = None if value in (None, "") else float(value)
            except (TypeError, ValueError):
                values[key] = None
        return BookSnapshot(
            instrument.instrument_id, self.venue, at or now, now, bids, asks, capacity=capacity, **values
        )

    def replace(self, order: Order, quantity: Decimal, limit_price: Decimal) -> None:
        raise VenueError("REPLACE_UNSUPPORTED", f"{self.venue} does not support native replace")

    def restore_working(self, order: Order) -> None:  # noqa: B027 - optional hook
        """Nothing to rebuild locally; working orders are refreshed from the venue by polling."""

    def close(self) -> None:
        self._http.close()
