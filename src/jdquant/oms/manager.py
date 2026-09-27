"""Order Management System: single authority for order state (Chapter 21, CON-003)."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from jdquant.core.clock import Clock
from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.core.events import EventBus
from jdquant.core.ids import IdGenerator, UuidIds
from jdquant.marketdata.instruments import InstrumentRegistry, InstrumentStatus
from jdquant.oms.orders import (
    ORDER_STATE_MACHINE,
    ExecutionReport,
    Fill,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    ReportType,
    StateTransition,
    TimeInForce,
    manual_deployment_id,
)

log = logging.getLogger(__name__)
S = OrderStatus


class RiskUnavailable(Exception):
    """Raised by a risk gate that could not produce a decision in time."""


class RiskGate(Protocol):
    def evaluate(self, order: Order): ...


class ExecutionRouter(Protocol):
    def submit(self, order: Order) -> None: ...
    def cancel(self, order: Order) -> None: ...
    def query(self, order: Order) -> None: ...


EligibilityCheck = Callable[[Order], str | None]


class OrderManager:
    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        instruments: InstrumentRegistry,
        risk: RiskGate,
        router: ExecutionRouter,
        *,
        eligibility: EligibilityCheck | None = None,
        ids: IdGenerator | None = None,
        ack_timeout: timedelta = timedelta(seconds=5),
        idempotency_window: timedelta = timedelta(hours=24),
        round_to_increments: bool = False,
    ):
        self._clock = clock
        self._bus = bus
        self._instruments = instruments
        self._risk = risk
        self._router = router
        self._eligibility = eligibility or (lambda order: None)
        self._ids = ids or UuidIds()
        self._ack_timeout = ack_timeout
        self._idempotency_window = idempotency_window
        self._round = round_to_increments
        self._orders: dict[str, Order] = {}
        self._by_client_id: dict[str, Order] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, datetime]] = {}
        self._fill_keys: set[tuple[str, str, str]] = set()
        self.fills: list[Fill] = []
        self.duplicate_fill_count = 0

    # ---- intake -----------------------------------------------------------------------------

    def submit(self, request: OrderRequest) -> Order:
        existing = self._idempotent_hit(request)
        if existing is not None:
            return existing

        order = self._create(request)
        violations = self._validate(order)
        if violations:
            self._reject(
                order,
                "ORDER_VALIDATION_FAILED",
                "; ".join(f"{v['field']}: {v['message']}" for v in violations),
            )
            return order

        self._transition(order, S.PENDING_RISK)
        blocked = self._eligibility(order)
        if blocked:
            order.reject_code, order.reject_reason = blocked, "trading eligibility conditions not met"
            self._transition(order, S.RISK_REJECTED, blocked)
            return order

        try:
            decision = self._risk.evaluate(order)
        except RiskUnavailable:
            decision = None
        if decision is None:
            order.reject_code, order.reject_reason = "RISK_UNAVAILABLE", "no risk decision within timeout"
            self._transition(order, S.RISK_REJECTED, "RISK_UNAVAILABLE")
            return order
        order.risk_decision_id = decision.decision_id
        if not decision.approved:
            order.reject_code, order.reject_reason = decision.reason_code, "rejected by pre-trade risk"
            self._transition(order, S.RISK_REJECTED, decision.reason_code)
            return order

        self._transition(order, S.PENDING_SUBMIT)
        order.submitted_at = self._clock.now()
        self._transition(order, S.SUBMITTED)
        self._router.submit(order)
        return order

    def _idempotent_hit(self, request: OrderRequest) -> Order | None:
        if not request.idempotency_key:
            return None
        key = (request.submitter, request.idempotency_key)
        hit = self._idempotency.get(key)
        if hit and self._clock.now() - hit[1] <= self._idempotency_window:
            return self._orders[hit[0]]
        return None

    def _create(self, request: OrderRequest) -> Order:
        tif = request.time_in_force or (
            TimeInForce.IOC if request.order_type is OrderType.MARKET else TimeInForce.GTC
        )
        order = Order(
            order_id=self._ids.next("ORD"),
            client_order_id=self._ids.next("JQ"),
            account_id=request.account_id,
            deployment_id=request.deployment_id or manual_deployment_id(request.account_id),
            instrument_id=request.instrument_id,
            side=request.side,
            order_type=request.order_type,
            time_in_force=tif,
            quantity=request.quantity,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            post_only=request.post_only,
            reduce_only=request.reduce_only,
            source=request.source,
            submitter=request.submitter,
            created_at=self._clock.now(),
            signal_id=request.signal_id,
            idempotency_key=request.idempotency_key,
            tags=dict(request.tags),
        )
        self._orders[order.order_id] = order
        self._by_client_id[order.client_order_id] = order
        if request.idempotency_key:
            self._idempotency[(request.submitter, request.idempotency_key)] = (
                order.order_id,
                self._clock.now(),
            )
        self._publish("order.created", order)
        return order

    def _validate(self, order: Order) -> list[dict[str, str]]:
        """Field and instrument-rule validation (FR-21004 – FR-21008)."""
        v: list[dict[str, str]] = []
        try:
            instrument = self._instruments.get(order.instrument_id)
        except NotFoundError:
            return [{"field": "instrument_id", "message": "INSTRUMENT_NOT_FOUND"}]
        if instrument.status is not InstrumentStatus.ACTIVE:
            v.append({"field": "instrument_id", "message": f"instrument is {instrument.status.value}"})

        if self._round:
            order.quantity = instrument.round_quantity_down(order.quantity)
            if order.limit_price is not None:
                order.limit_price = instrument.round_price_passive(order.limit_price, order.side)

        needs_limit = order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = order.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
        if needs_limit and order.limit_price is None:
            v.append({"field": "limit_price", "message": "required for this order type"})
        if not needs_limit and order.limit_price is not None:
            v.append({"field": "limit_price", "message": "not allowed for this order type"})
        if needs_stop and order.stop_price is None:
            v.append({"field": "stop_price", "message": "required for this order type"})
        if order.post_only and order.order_type is not OrderType.LIMIT:
            v.append({"field": "post_only", "message": "only valid for LIMIT orders"})
        if order.time_in_force is TimeInForce.GTD:
            v.append({"field": "time_in_force", "message": "GTD is not supported in this release"})

        if not instrument.is_quantity_aligned(order.quantity):
            v.append(
                {
                    "field": "quantity",
                    "message": f"must be a positive multiple of lot size {instrument.lot_size}",
                }
            )
        elif order.quantity < instrument.min_quantity:
            v.append({"field": "quantity", "message": f"below minimum {instrument.min_quantity}"})
        if instrument.max_quantity is not None and order.quantity > instrument.max_quantity:
            v.append({"field": "quantity", "message": f"above maximum {instrument.max_quantity}"})
        for name in ("limit_price", "stop_price"):
            price = getattr(order, name)
            if price is not None and not instrument.is_price_aligned(price):
                v.append(
                    {
                        "field": name,
                        "message": f"must be a positive multiple of tick size {instrument.tick_size}",
                    }
                )
        if instrument.min_notional is not None and order.limit_price is not None:
            if instrument.notional(order.quantity, order.limit_price) < instrument.min_notional:
                v.append(
                    {"field": "quantity", "message": f"notional below minimum {instrument.min_notional}"}
                )
        return v

    # ---- cancellation -----------------------------------------------------------------------

    def cancel(self, order_id: str) -> Order:
        order = self.get(order_id)
        if order.status is S.PENDING_CANCEL:
            return order
        if order.status in (S.PENDING_SUBMIT, S.TRIGGER_PENDING):
            self._transition(order, S.CANCELED, "canceled before submission")
            return order
        if order.status not in (S.OPEN, S.PARTIALLY_FILLED):
            raise PlatformError(
                "ORDER_NOT_MODIFIABLE", f"order in state {order.status.value} cannot be canceled"
            )
        order.status_before_cancel = order.status
        self._transition(order, S.PENDING_CANCEL)
        self._router.cancel(order)
        return order

    def cancel_all(
        self,
        *,
        account_id: str | None = None,
        deployment_id: str | None = None,
        instrument_id: str | None = None,
    ) -> list[Order]:
        canceled = []
        for order in self.list_orders(working_only=True):
            if account_id and order.account_id != account_id:
                continue
            if deployment_id and order.deployment_id != deployment_id:
                continue
            if instrument_id and order.instrument_id != instrument_id:
                continue
            if order.status in (S.OPEN, S.PARTIALLY_FILLED, S.PENDING_SUBMIT, S.TRIGGER_PENDING):
                canceled.append(self.cancel(order.order_id))
        return canceled

    # ---- execution reports ------------------------------------------------------------------

    def on_execution_report(self, report: ExecutionReport) -> None:
        order = self._by_client_id.get(report.client_order_id)
        if order is None:
            self._bus.publish(
                "order.state.anomaly",
                {"client_order_id": report.client_order_id, "description": "report for unknown order"},
                producer="oms",
            )
            return
        match report.report_type:
            case ReportType.ACK:
                order.venue_order_id = report.venue_order_id or order.venue_order_id
                if order.status in (S.SUBMITTED, S.UNKNOWN):
                    order.acknowledged_at = report.exchange_ts
                    self._transition(order, S.OPEN)
            case ReportType.REJECT:
                if order.status in (S.SUBMITTED, S.UNKNOWN):
                    order.reject_code, order.reject_reason = (
                        report.reason or "VENUE_ERROR",
                        "rejected by venue",
                    )
                    self._transition(order, S.REJECTED, report.reason)
            case ReportType.FILL:
                self._apply_fill(order, report)
            case ReportType.CANCELED:
                if not order.is_terminal:
                    self._transition(order, S.CANCELED, report.reason)
            case ReportType.CANCEL_REJECT:
                if order.status is S.PENDING_CANCEL and order.status_before_cancel is not None:
                    target = S.PARTIALLY_FILLED if order.filled_quantity > 0 else S.OPEN
                    self._transition(order, target, "cancel rejected")
            case ReportType.EXPIRED:
                if not order.is_terminal:
                    self._transition(order, S.EXPIRED, report.reason)
            case ReportType.NOT_FOUND:
                if order.status is S.UNKNOWN:
                    order.reject_code, order.reject_reason = (
                        "NOT_RECEIVED",
                        "venue has no record of the order",
                    )
                    self._transition(order, S.REJECTED, "NOT_RECEIVED")

    def _apply_fill(self, order: Order, report: ExecutionReport) -> None:
        assert report.price is not None and report.quantity is not None and report.venue_trade_id
        key = (report.venue, order.instrument_id, report.venue_trade_id)
        if key in self._fill_keys:
            self.duplicate_fill_count += 1
            return
        self._fill_keys.add(key)

        previous_notional = (order.average_fill_price or Decimal(0)) * order.filled_quantity
        order.filled_quantity += report.quantity
        order.average_fill_price = (
            previous_notional + report.price * report.quantity
        ) / order.filled_quantity
        if report.fee_asset:
            order.fees[report.fee_asset] = order.fees.get(report.fee_asset, Decimal(0)) + report.fee

        fill = Fill(
            fill_id=self._ids.next("FILL"),
            order_id=order.order_id,
            account_id=order.account_id,
            deployment_id=order.deployment_id,
            instrument_id=order.instrument_id,
            side=order.side,
            price=report.price,
            quantity=report.quantity,
            fee=report.fee,
            fee_asset=report.fee_asset,
            liquidity=report.liquidity,
            venue_trade_id=report.venue_trade_id,
            exchange_ts=report.exchange_ts,
            is_simulated=report.is_simulated,
            venue=report.venue,
        )
        self.fills.append(fill)

        if order.filled_quantity > order.quantity:
            order.overfilled = True
            self._bus.publish(
                "order.state.anomaly",
                {"order_id": order.order_id, "description": "overfill reported by venue"},
                producer="oms",
                partition_key=order.account_id,
            )

        # Positions are updated from the fill before the order state change is published.
        self._bus.publish(
            "order.fill",
            {"fill": fill, "order": copy.copy(order)},
            producer="oms",
            partition_key=order.account_id,
        )
        if order.is_terminal:
            self._bus.publish(
                "order.state.anomaly",
                {
                    "order_id": order.order_id,
                    "description": f"fill received in terminal state {order.status}",
                },
                producer="oms",
                partition_key=order.account_id,
            )
        elif order.filled_quantity >= order.quantity:
            self._transition(order, S.FILLED)
        elif order.status in (S.SUBMITTED, S.OPEN, S.UNKNOWN, S.PENDING_REPLACE):
            self._transition(order, S.PARTIALLY_FILLED)

    # ---- timeouts ---------------------------------------------------------------------------

    def check_timeouts(self) -> list[Order]:
        """Move unacknowledged orders to UNKNOWN and query the venue (FR-21046, FR-21047)."""
        now = self._clock.now()
        timed_out = []
        for order in self._orders.values():
            if (
                order.status is S.SUBMITTED
                and order.submitted_at
                and now - order.submitted_at > self._ack_timeout
            ):
                self._transition(order, S.UNKNOWN, "acknowledgment timeout")
                self._router.query(order)
                timed_out.append(order)
        return timed_out

    # ---- recovery (FR-51002) ----------------------------------------------------------------

    def restore(self, orders: Iterable[Order], fills: Iterable[Fill]) -> None:
        for order in orders:
            self._orders[order.order_id] = order
            self._by_client_id[order.client_order_id] = order
            if order.idempotency_key:
                self._idempotency[(order.submitter, order.idempotency_key)] = (
                    order.order_id,
                    order.created_at,
                )
        for fill in fills:
            self._fill_keys.add((fill.venue, fill.instrument_id, fill.venue_trade_id))
            self.fills.append(fill)

    def resolve_in_flight(self) -> list[str]:
        """Settle orders whose last persisted state was mid-flight when the platform stopped."""
        actions = []
        for order in list(self._orders.values()):
            match order.status:
                case S.CREATED:
                    self._reject(order, "RECOVERY", "platform restarted before validation")
                case S.PENDING_RISK:
                    order.reject_code, order.reject_reason = (
                        "RECOVERY",
                        "platform restarted before risk decision",
                    )
                    self._transition(order, S.RISK_REJECTED, "RECOVERY")
                case S.PENDING_SUBMIT:
                    self._transition(order, S.CANCELED, "not sent before restart")
                case S.SUBMITTED:
                    self._transition(order, S.UNKNOWN, "unacknowledged at restart")
                    self._router.query(order)
                case S.UNKNOWN:
                    self._router.query(order)
                case S.PENDING_CANCEL:
                    self._router.cancel(order)
                case _:
                    continue
            actions.append(f"{order.order_id}: {order.status.value}")
        return actions

    # ---- queries ----------------------------------------------------------------------------

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise NotFoundError("ORDER_NOT_FOUND", f"unknown order {order_id}") from None

    def list_orders(
        self,
        *,
        account_id: str | None = None,
        deployment_id: str | None = None,
        working_only: bool = False,
        statuses: Iterable[OrderStatus] | None = None,
    ) -> list[Order]:
        wanted = set(statuses) if statuses else None
        return [
            o
            for o in self._orders.values()
            if (account_id is None or o.account_id == account_id)
            and (deployment_id is None or o.deployment_id == deployment_id)
            and (not working_only or o.is_working or o.status in (S.PENDING_SUBMIT, S.TRIGGER_PENDING))
            and (wanted is None or o.status in wanted)
        ]

    # ---- internals --------------------------------------------------------------------------

    def _reject(self, order: Order, code: str, reason: str) -> None:
        order.reject_code, order.reject_reason = code, reason
        self._transition(order, S.REJECTED, code)

    def _transition(self, order: Order, target: OrderStatus, reason: str | None = None) -> None:
        ORDER_STATE_MACHINE.assert_transition(order.status, target)
        order.history.append(StateTransition(order.status, target, self._clock.now(), reason))
        previous = order.status
        order.status = target
        if order.is_terminal:
            order.terminal_at = self._clock.now()
        self._bus.publish(
            "order.state.changed",
            {"order": copy.copy(order), "from": previous.value, "to": target.value, "reason": reason},
            producer="oms",
            partition_key=order.account_id,
        )

    def _publish(self, event_type: str, order: Order) -> None:
        self._bus.publish(
            event_type, {"order": copy.copy(order)}, producer="oms", partition_key=order.account_id
        )
