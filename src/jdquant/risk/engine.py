"""Risk Management System: pre-trade checks and post-trade loss control (Chapter 27)."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from jdquant.core.clock import Clock
from jdquant.core.events import Event, EventBus
from jdquant.core.ids import IdGenerator, UuidIds
from jdquant.core.types import ZERO, Side
from jdquant.marketdata.cache import MarketDataCache
from jdquant.marketdata.instruments import InstrumentRegistry
from jdquant.oms.orders import Order, OrderType

log = logging.getLogger(__name__)


class LimitType(StrEnum):
    # Declaration order is the reason-code precedence of Chapter 27.4 / FR-27023.
    MAX_ORDER_QUANTITY = "MAX_ORDER_QUANTITY"
    MAX_ORDER_NOTIONAL = "MAX_ORDER_NOTIONAL"
    PRICE_DEVIATION = "PRICE_DEVIATION"
    MAX_POSITION_QUANTITY = "MAX_POSITION_QUANTITY"
    MAX_POSITION_NOTIONAL = "MAX_POSITION_NOTIONAL"
    MAX_OPEN_ORDERS = "MAX_OPEN_ORDERS"
    MAX_ORDER_RATE = "MAX_ORDER_RATE"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    RESTRICTED_INSTRUMENT = "RESTRICTED_INSTRUMENT"
    STALE_DATA = "STALE_DATA"
    REDUCE_ONLY = "REDUCE_ONLY"


# Checks that still apply to orders that strictly reduce a position (FR-27024).
NON_WAIVABLE = frozenset(
    {LimitType.RESTRICTED_INSTRUMENT, LimitType.PRICE_DEVIATION, LimitType.MAX_ORDER_RATE}
)


class Scope(StrEnum):
    WORKSPACE = "WORKSPACE"
    ACCOUNT = "ACCOUNT"
    DEPLOYMENT = "DEPLOYMENT"
    INSTRUMENT = "INSTRUMENT"


class BreachAction(StrEnum):
    REJECT = "REJECT"
    REDUCE_ONLY = "REDUCE_ONLY"
    KILL_SWITCH_BLOCK_NEW = "KILL_SWITCH_BLOCK_NEW"
    KILL_SWITCH_CANCEL = "KILL_SWITCH_CANCEL"
    KILL_SWITCH_FLATTEN = "KILL_SWITCH_FLATTEN"


@dataclass(frozen=True)
class RiskLimit:
    limit_type: LimitType
    threshold: Decimal
    action: BreachAction = BreachAction.REJECT


@dataclass
class RiskProfile:
    name: str
    scope: Scope
    limits: list[RiskLimit]
    target_id: str | None = None
    restricted_instruments: set[str] = field(default_factory=set)
    active: bool = True

    def applies_to(self, order: Order) -> bool:
        if not self.active:
            return False
        match self.scope:
            case Scope.WORKSPACE:
                return True
            case Scope.ACCOUNT:
                return self.target_id == order.account_id
            case Scope.DEPLOYMENT:
                return self.target_id == order.deployment_id
            case Scope.INSTRUMENT:
                return self.target_id == order.instrument_id


@dataclass(frozen=True)
class CheckResult:
    limit_type: LimitType
    projected: Decimal | None
    threshold: Decimal | None
    passed: bool


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    order_id: str
    approved: bool
    reason_code: str | None
    checks: tuple[CheckResult, ...]
    evaluated_at: datetime


@dataclass
class _PositionState:
    quantity: Decimal = ZERO
    average_entry_price: Decimal = ZERO
    multiplier: Decimal = Decimal(1)


@dataclass
class _WorkingOrder:
    account_id: str
    instrument_id: str
    side: Side
    remaining: Decimal


class RiskEngine:
    """Fails closed (BR-27-01); keeps all pre-trade state in memory (FR-27028)."""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        instruments: InstrumentRegistry,
        market: MarketDataCache,
        profiles: list[RiskProfile] | None = None,
        *,
        ids: IdGenerator | None = None,
        allow_reducing_on_stale: bool = True,
    ):
        self._clock = clock
        self._bus = bus
        self._instruments = instruments
        self._market = market
        self._ids = ids or UuidIds()
        self.profiles: list[RiskProfile] = list(profiles or [])
        self.allow_reducing_on_stale = allow_reducing_on_stale
        self._positions: dict[tuple[str, str, str], _PositionState] = defaultdict(_PositionState)
        self._working: dict[str, _WorkingOrder] = {}
        self._order_times: dict[str, deque[datetime]] = defaultdict(deque)
        self._realized_today: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self._unrealized_baseline: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self._day: date | None = None
        self.reduce_only_accounts: set[str] = set()
        self.decisions: dict[str, RiskDecision] = {}
        bus.subscribe("order.state.changed", self._on_order_state)
        bus.subscribe("order.fill", self._on_fill)
        bus.subscribe("position.updated", self._on_position_updated)

    # ---- pre-trade -------------------------------------------------------------------------

    def evaluate(self, order: Order) -> RiskDecision:
        self._roll_day()
        try:
            checks = self._run_checks(order)
        except Exception:
            log.exception("risk evaluation failed for %s", order.order_id)
            return self._record(order, False, "RISK_EVALUATION_ERROR", ())
        failed = [c for c in checks if not c.passed]
        if self._is_reducing(order):
            failed = [c for c in failed if c.limit_type in NON_WAIVABLE]
        reason = f"RISK_{failed[0].limit_type.value}" if failed else None
        decision = self._record(order, not failed, reason, tuple(checks))
        if decision.approved:
            self._order_times[order.account_id].append(self._clock.now())
        return decision

    def _record(self, order: Order, approved: bool, reason: str | None, checks: tuple[CheckResult, ...]):
        decision = RiskDecision(
            self._ids.next("RD"), order.order_id, approved, reason, checks, self._clock.now()
        )
        self.decisions[decision.decision_id] = decision
        return decision

    def _effective_limits(self, order: Order) -> dict[LimitType, Decimal]:
        limits: dict[LimitType, Decimal] = {}
        for profile in self.profiles:
            if profile.applies_to(order):
                for limit in profile.limits:
                    current = limits.get(limit.limit_type)
                    limits[limit.limit_type] = (
                        limit.threshold if current is None else min(current, limit.threshold)
                    )
        return limits

    def _run_checks(self, order: Order) -> list[CheckResult]:
        instrument = self._instruments.get(order.instrument_id)
        limits = self._effective_limits(order)
        reference = self._market.reference_price(order.instrument_id)
        stale = reference is None or self._market.is_stale(order.instrument_id)
        price = order.limit_price if order.order_type is OrderType.LIMIT else reference
        results: list[CheckResult] = []

        def check(limit_type: LimitType, projected: Decimal | None, passed_fn) -> None:
            threshold = limits.get(limit_type)
            if threshold is not None:
                results.append(CheckResult(limit_type, projected, threshold, passed_fn(projected, threshold)))

        check(LimitType.MAX_ORDER_QUANTITY, order.quantity, lambda p, t: p <= t)
        notional = instrument.notional(order.quantity, price) if price is not None else None
        check(LimitType.MAX_ORDER_NOTIONAL, notional, lambda p, t: p is not None and p <= t)
        if order.order_type is OrderType.LIMIT and reference is not None and order.limit_price is not None:
            deviation = abs(order.limit_price - reference) / reference
            check(LimitType.PRICE_DEVIATION, deviation, lambda p, t: p <= t)

        projected_qty = self._projected_position(order)
        check(LimitType.MAX_POSITION_QUANTITY, abs(projected_qty), lambda p, t: p <= t)
        projected_notional = abs(projected_qty) * price * instrument.contract_multiplier if price else None
        check(LimitType.MAX_POSITION_NOTIONAL, projected_notional, lambda p, t: p is not None and p <= t)

        open_orders = Decimal(sum(1 for w in self._working.values() if w.account_id == order.account_id) + 1)
        check(LimitType.MAX_OPEN_ORDERS, open_orders, lambda p, t: p <= t)
        check(
            LimitType.MAX_ORDER_RATE,
            Decimal(self._recent_order_count(order.account_id) + 1),
            lambda p, t: p <= t,
        )

        restricted = any(
            order.instrument_id in p.restricted_instruments for p in self.profiles if p.applies_to(order)
        )
        results.append(CheckResult(LimitType.RESTRICTED_INSTRUMENT, None, None, not restricted))

        stale_ok = not stale or (self.allow_reducing_on_stale and self._is_reducing(order))
        results.append(CheckResult(LimitType.STALE_DATA, None, None, stale_ok))

        reduce_only_ok = self._is_reducing(order) or not (
            order.reduce_only or order.account_id in self.reduce_only_accounts
        )
        results.append(CheckResult(LimitType.REDUCE_ONLY, None, None, reduce_only_ok))
        return results

    def _projected_position(self, order: Order) -> Decimal:
        """Conservative projection including same-side working orders (FR-27021)."""
        current = self.net_position(order.account_id, order.instrument_id)
        same_side = sum(
            (
                w.remaining
                for w in self._working.values()
                if w.account_id == order.account_id
                and w.instrument_id == order.instrument_id
                and w.side == order.side
            ),
            ZERO,
        )
        return current + order.side.sign * (same_side + order.quantity)

    def net_position(self, account_id: str, instrument_id: str) -> Decimal:
        return sum(
            (
                p.quantity
                for (a, i, _), p in self._positions.items()
                if a == account_id and i == instrument_id
            ),
            ZERO,
        )

    def _is_reducing(self, order: Order) -> bool:
        current = self.net_position(order.account_id, order.instrument_id)
        return (
            current != 0 and order.side.sign != (1 if current > 0 else -1) and order.quantity <= abs(current)
        )

    def _recent_order_count(self, account_id: str) -> int:
        window = self._order_times[account_id]
        cutoff = self._clock.now() - timedelta(seconds=1)
        while window and window[0] <= cutoff:
            window.popleft()
        return len(window)

    # ---- post-trade ------------------------------------------------------------------------

    def daily_pnl(self, account_id: str) -> Decimal:
        self._roll_day()
        return (
            self._realized_today[account_id]
            + self._unrealized(account_id)
            - self._unrealized_baseline[account_id]
        )

    def evaluate_post_trade(self, account_id: str) -> None:
        """Daily loss control (FR-27040 – FR-27044, BR-27-03)."""
        pnl = self.daily_pnl(account_id)
        for profile in self.profiles:
            if not profile.active or (profile.scope is Scope.ACCOUNT and profile.target_id != account_id):
                continue
            if profile.scope not in (Scope.WORKSPACE, Scope.ACCOUNT):
                continue
            for limit in profile.limits:
                if limit.limit_type is LimitType.MAX_DAILY_LOSS and -pnl >= limit.threshold:
                    self._breach(account_id, limit, pnl)

    def _breach(self, account_id: str, limit: RiskLimit, pnl: Decimal) -> None:
        if account_id in self.reduce_only_accounts:
            return
        self.reduce_only_accounts.add(account_id)
        self._publish_state()
        self._bus.publish(
            "risk.breach.detected",
            {
                "account_id": account_id,
                "limit_type": limit.limit_type.value,
                "threshold": limit.threshold,
                "value": pnl,
                "action": limit.action.value,
            },
            producer="rms",
            partition_key=account_id,
        )

    def release_reduce_only(self, account_id: str) -> None:
        self.reduce_only_accounts.discard(account_id)
        self._publish_state()

    def set_profiles(self, profiles: list[RiskProfile]) -> None:
        self.profiles = list(profiles)
        self._bus.publish("risk.profile.changed", {"count": len(self.profiles)}, producer="rms")

    # ---- persistence & recovery (FR-92002, FR-92004) ----------------------------------------

    def state_snapshot(self) -> dict:
        return {
            "day": self._day.isoformat() if self._day else None,
            "baselines": {k: str(v) for k, v in self._unrealized_baseline.items()},
            "reduce_only": sorted(self.reduce_only_accounts),
        }

    def _publish_state(self) -> None:
        self._bus.publish("risk.state.changed", self.state_snapshot(), producer="rms")

    def rebuild(
        self,
        positions: list[tuple[str, str, str, Decimal, Decimal, Decimal]],
        working: list[Order],
        realized_today: dict[str, Decimal],
        state: dict | None,
    ) -> None:
        """Restore in-memory pre-trade state after a restart without re-running post-trade actions."""
        self._positions.clear()
        for account_id, instrument_id, deployment_id, qty, avg, mult in positions:
            self._positions[(account_id, instrument_id, deployment_id)] = _PositionState(qty, avg, mult)
        self._working = {
            o.order_id: _WorkingOrder(o.account_id, o.instrument_id, o.side, o.remaining_quantity)
            for o in working
        }
        today = self._clock.now().date()
        if state and state.get("day") == today.isoformat():
            self._day = today
            self._unrealized_baseline.update({k: Decimal(v) for k, v in state["baselines"].items()})
            self.reduce_only_accounts = set(state["reduce_only"])
            self._realized_today = defaultdict(lambda: ZERO, realized_today)
        else:
            self._day = None

    def _unrealized(self, account_id: str) -> Decimal:
        total = ZERO
        for (acct, instrument_id, _), pos in self._positions.items():
            if acct != account_id or pos.quantity == 0:
                continue
            price = self._market.reference_price(instrument_id)
            if price is not None:
                total += pos.quantity * (price - pos.average_entry_price) * pos.multiplier
        return total

    def _roll_day(self) -> None:
        today = self._clock.now().date()
        if self._day == today:
            return
        self._day = today
        accounts = {acct for acct, _, _ in self._positions} | set(self._realized_today)
        self._realized_today.clear()
        for account_id in accounts:
            self._unrealized_baseline[account_id] = self._unrealized(account_id)
        self.reduce_only_accounts.clear()
        self._publish_state()

    # ---- event consumers -------------------------------------------------------------------

    def _on_order_state(self, event: Event) -> None:
        order: Order = event.payload["order"]
        if order.is_working:
            self._working[order.order_id] = _WorkingOrder(
                order.account_id, order.instrument_id, order.side, order.remaining_quantity
            )
        else:
            self._working.pop(order.order_id, None)

    def _on_fill(self, event: Event) -> None:
        self._on_order_state(event)

    def _on_position_updated(self, event: Event) -> None:
        self._roll_day()
        p = event.payload
        state = self._positions[(p["account_id"], p["instrument_id"], p["deployment_id"])]
        state.quantity = p["quantity"]
        state.average_entry_price = p["average_entry_price"]
        state.multiplier = p["multiplier"]
        self._realized_today[p["account_id"]] += p["realized_delta"] - p["fee"]
        self.evaluate_post_trade(p["account_id"])
