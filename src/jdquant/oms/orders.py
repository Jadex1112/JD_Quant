"""Order, fill and execution report domain model (Chapter 21.3–21.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from jdquant.core.state_machine import StateMachine
from jdquant.core.types import ZERO, Side


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    DAY = "DAY"
    GTD = "GTD"


class OrderSource(StrEnum):
    STRATEGY = "STRATEGY"
    MANUAL = "MANUAL"
    API = "API"
    SYSTEM = "SYSTEM"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    PENDING_RISK = "PENDING_RISK"
    RISK_REJECTED = "RISK_REJECTED"
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    PENDING_REPLACE = "PENDING_REPLACE"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    TRIGGER_PENDING = "TRIGGER_PENDING"


S = OrderStatus
TERMINAL_STATES = frozenset({S.RISK_REJECTED, S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED})
WORKING_STATES = frozenset(
    {
        S.SUBMITTED,
        S.OPEN,
        S.PARTIALLY_FILLED,
        S.PENDING_CANCEL,
        S.PENDING_REPLACE,
        S.UNKNOWN,
        S.TRIGGER_PENDING,
    }
)

ORDER_STATE_MACHINE = StateMachine(
    "Order",
    {
        S.CREATED: {S.PENDING_RISK, S.REJECTED},
        S.PENDING_RISK: {S.PENDING_SUBMIT, S.RISK_REJECTED, S.TRIGGER_PENDING},
        S.TRIGGER_PENDING: {S.PENDING_RISK, S.CANCELED},
        S.PENDING_SUBMIT: {S.SUBMITTED, S.CANCELED},
        S.SUBMITTED: {S.OPEN, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED, S.UNKNOWN},
        S.OPEN: {S.PARTIALLY_FILLED, S.FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE, S.EXPIRED, S.CANCELED},
        S.PARTIALLY_FILLED: {S.FILLED, S.PENDING_CANCEL, S.PENDING_REPLACE, S.EXPIRED, S.CANCELED},
        S.PENDING_CANCEL: {S.CANCELED, S.FILLED, S.PARTIALLY_FILLED, S.OPEN},
        S.PENDING_REPLACE: {S.OPEN, S.PARTIALLY_FILLED, S.FILLED},
        S.UNKNOWN: {s for s in OrderStatus if s is not S.UNKNOWN},
    },
)


def manual_deployment_id(account_id: str) -> str:
    """Pseudo-deployment for manual orders (BR-19-04)."""
    return f"MANUAL:{account_id}"


@dataclass
class OrderRequest:
    account_id: str
    instrument_id: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce | None = None
    deployment_id: str | None = None
    post_only: bool = False
    reduce_only: bool = False
    source: OrderSource = OrderSource.MANUAL
    submitter: str = "system"
    idempotency_key: str | None = None
    signal_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class StateTransition:
    from_status: OrderStatus
    to_status: OrderStatus
    at: datetime
    reason: str | None


@dataclass
class Order:
    order_id: str
    client_order_id: str
    account_id: str
    deployment_id: str
    instrument_id: str
    side: Side
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    post_only: bool
    reduce_only: bool
    source: OrderSource
    submitter: str
    created_at: datetime
    signal_id: str | None = None
    idempotency_key: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = ZERO
    average_fill_price: Decimal | None = None
    fees: dict[str, Decimal] = field(default_factory=dict)
    venue_order_id: str | None = None
    risk_decision_id: str | None = None
    reject_code: str | None = None
    reject_reason: str | None = None
    submitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    terminal_at: datetime | None = None
    overfilled: bool = False
    history: list[StateTransition] = field(default_factory=list)
    status_before_cancel: OrderStatus | None = None
    pending_quantity: Decimal | None = None
    pending_limit_price: Decimal | None = None
    replace_via_cancel: bool = False
    replaces_order_id: str | None = None
    replaced_by_order_id: str | None = None

    @property
    def remaining_quantity(self) -> Decimal:
        return max(self.quantity - self.filled_quantity, ZERO)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def is_working(self) -> bool:
        return self.status in WORKING_STATES


class Liquidity(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    account_id: str
    deployment_id: str
    instrument_id: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_asset: str
    liquidity: Liquidity
    venue_trade_id: str
    exchange_ts: datetime
    is_simulated: bool = False
    venue: str = ""


class ReportType(StrEnum):
    ACK = "ACK"
    REJECT = "REJECT"
    FILL = "FILL"
    CANCELED = "CANCELED"
    CANCEL_REJECT = "CANCEL_REJECT"
    EXPIRED = "EXPIRED"
    NOT_FOUND = "NOT_FOUND"
    REPLACED = "REPLACED"
    REPLACE_REJECT = "REPLACE_REJECT"


@dataclass(frozen=True)
class ExecutionReport:
    """Normalized venue response (Chapter 22.3.2)."""

    report_type: ReportType
    venue: str
    client_order_id: str
    exchange_ts: datetime
    venue_order_id: str | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None
    fee: Decimal = ZERO
    fee_asset: str = ""
    liquidity: Liquidity = Liquidity.UNKNOWN
    venue_trade_id: str | None = None
    reason: str | None = None
    is_simulated: bool = False
    new_client_order_id: str | None = None
