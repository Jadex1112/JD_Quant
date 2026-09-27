"""Trading Engine: accounts, deployment lifecycle, kill switches and eligibility (Chapter 19)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from jdquant.core.clock import Clock
from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.core.events import Event, EventBus
from jdquant.core.ids import IdGenerator, UuidIds
from jdquant.core.state_machine import StateMachine
from jdquant.core.types import Side
from jdquant.oms.manager import OrderManager
from jdquant.oms.orders import Order, OrderRequest, OrderSource, OrderType
from jdquant.positions.engine import PositionEngine


class AccountMode(StrEnum):
    LIVE = "LIVE"
    PAPER = "PAPER"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


@dataclass
class TradingAccount:
    account_id: str
    name: str
    venue: str
    mode: AccountMode
    base_currency: str
    status: AccountStatus = AccountStatus.ACTIVE


class DeploymentState(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FLATTENING = "FLATTENING"
    HALTED = "HALTED"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


D = DeploymentState
_NON_TERMINAL = {s for s in DeploymentState if s is not D.RETIRED}
DEPLOYMENT_STATE_MACHINE = StateMachine(
    "Deployment",
    {
        D.DRAFT: {D.READY, D.FAILED},
        D.READY: {D.STARTING, D.RETIRED, D.FAILED},
        D.STARTING: {D.RUNNING, D.FAILED, D.HALTED},
        D.RUNNING: {D.PAUSED, D.STOPPING, D.FLATTENING, D.HALTED, D.FAILED},
        D.PAUSED: {D.RUNNING, D.STOPPING, D.FLATTENING, D.HALTED, D.FAILED},
        D.STOPPING: {D.STOPPED, D.FAILED},
        D.STOPPED: {D.STARTING, D.FLATTENING, D.RETIRED, D.FAILED},
        D.FLATTENING: {D.STOPPED, D.HALTED, D.FAILED},
        D.HALTED: {D.STOPPED, D.FAILED},
        D.FAILED: {D.STOPPED},
    },
)


@dataclass
class Deployment:
    deployment_id: str
    strategy_name: str
    strategy_version: str
    account_id: str
    mode: AccountMode
    parameters: dict[str, Any]
    instruments: list[str]
    created_by: str
    state: DeploymentState = DeploymentState.DRAFT
    state_reason: str | None = None
    approved_by: str | None = None
    history: list[tuple[DeploymentState, DeploymentState, datetime, str | None]] = field(default_factory=list)


class KillSwitchScope(StrEnum):
    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    INSTRUMENT = "INSTRUMENT"


class KillSwitchAction(StrEnum):
    BLOCK_NEW = "BLOCK_NEW"
    CANCEL_OPEN = "CANCEL_OPEN"
    FLATTEN = "FLATTEN"


@dataclass
class KillSwitch:
    kill_switch_id: str
    scope: KillSwitchScope
    target_id: str | None
    action: KillSwitchAction
    reason: str
    triggered_by: str
    triggered_at: datetime
    released_by: str | None = None
    released_at: datetime | None = None
    uncancelable_order_ids: list[str] = field(default_factory=list)
    release_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.released_at is None

    def covers(self, *, account_id: str, deployment_id: str, instrument_id: str | None) -> bool:
        match self.scope:
            case KillSwitchScope.GLOBAL:
                return True
            case KillSwitchScope.ACCOUNT:
                return self.target_id == account_id
            case KillSwitchScope.STRATEGY:
                return self.target_id == deployment_id
            case KillSwitchScope.INSTRUMENT:
                return instrument_id is not None and self.target_id == instrument_id


_KILL_SWITCH_BY_RISK_ACTION = {
    "KILL_SWITCH_BLOCK_NEW": KillSwitchAction.BLOCK_NEW,
    "KILL_SWITCH_CANCEL": KillSwitchAction.CANCEL_OPEN,
    "KILL_SWITCH_FLATTEN": KillSwitchAction.FLATTEN,
}


class TradingEngine:
    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        positions: PositionEngine,
        *,
        ids: IdGenerator | None = None,
        single_user: bool = False,
    ):
        self._clock = clock
        self._bus = bus
        self._positions = positions
        self._ids = ids or UuidIds()
        self._oms: OrderManager | None = None
        self.single_user = single_user
        self.accounts: dict[str, TradingAccount] = {}
        self.deployments: dict[str, Deployment] = {}
        self.kill_switches: dict[str, KillSwitch] = {}
        self.maintenance_mode = False
        bus.subscribe("risk.breach.detected", self._on_risk_breach)
        bus.subscribe("position.updated", self._on_position_updated)

    def attach_oms(self, oms: OrderManager) -> None:
        self._oms = oms

    @property
    def oms(self) -> OrderManager:
        if self._oms is None:
            raise RuntimeError("order manager not attached")
        return self._oms

    # ---- accounts ---------------------------------------------------------------------------

    def register_account(self, account: TradingAccount) -> TradingAccount:
        self.accounts[account.account_id] = account
        return account

    def get_account(self, account_id: str) -> TradingAccount:
        try:
            return self.accounts[account_id]
        except KeyError:
            raise NotFoundError("ACCOUNT_NOT_FOUND", f"unknown account {account_id}") from None

    def set_account_status(self, account_id: str, status: AccountStatus) -> TradingAccount:
        account = self.get_account(account_id)
        previous, account.status = account.status, status
        self._bus.publish(
            "account.status.changed",
            {"account_id": account_id, "from": previous.value, "to": status.value},
            producer="lte",
            partition_key=account_id,
        )
        return account

    # ---- deployments ------------------------------------------------------------------------

    def create_deployment(
        self,
        *,
        strategy_name: str,
        strategy_version: str,
        account_id: str,
        parameters: dict[str, Any],
        instruments: list[str],
        created_by: str,
    ) -> Deployment:
        account = self.get_account(account_id)
        for other in self.deployments.values():
            if (
                other.account_id == account_id
                and other.state not in (D.RETIRED, D.STOPPED, D.FAILED)
                and set(other.instruments) & set(instruments)
            ):
                raise PlatformError(
                    "INSTRUMENT_CONFLICT", f"instrument already traded by {other.deployment_id}"
                )
        deployment = Deployment(
            deployment_id=self._ids.next("DEP"),
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            account_id=account_id,
            mode=account.mode,
            parameters=dict(parameters),
            instruments=list(instruments),
            created_by=created_by,
        )
        self.deployments[deployment.deployment_id] = deployment
        self._bus.publish("deployment.created", {"deployment_id": deployment.deployment_id}, producer="lte")
        return deployment

    def get_deployment(self, deployment_id: str) -> Deployment:
        try:
            return self.deployments[deployment_id]
        except KeyError:
            raise NotFoundError("DEPLOYMENT_NOT_FOUND", f"unknown deployment {deployment_id}") from None

    def approve(self, deployment_id: str, approver: str) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        if deployment.mode is AccountMode.LIVE and approver == deployment.created_by and not self.single_user:
            raise PlatformError(
                "DEPLOYMENT_NOT_APPROVED", "live deployments require a different approver (CON-203)"
            )
        deployment.approved_by = approver
        self._transition(deployment, D.READY, f"approved by {approver}")
        return deployment

    def start(self, deployment_id: str) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        if self.get_account(deployment.account_id).status is not AccountStatus.ACTIVE:
            raise PlatformError("ACCOUNT_NOT_ACTIVE", "account is not active")
        if self.maintenance_mode:
            raise PlatformError("MAINTENANCE_MODE", "deployments cannot start during maintenance")
        if any(self._covering_switches(deployment.account_id, deployment_id, None)):
            raise PlatformError("KILL_SWITCH_ACTIVE", "a kill switch applies to this deployment")
        self._transition(deployment, D.STARTING)
        self._transition(deployment, D.RUNNING)
        return deployment

    def pause(self, deployment_id: str, reason: str = "paused by user") -> Deployment:
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.PAUSED, reason)
        return deployment

    def resume(self, deployment_id: str) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.RUNNING, "resumed")
        return deployment

    def stop(self, deployment_id: str, *, cancel_open: bool = True) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.STOPPING)
        if cancel_open:
            self.oms.cancel_all(deployment_id=deployment_id)
        self._transition(deployment, D.STOPPED)
        return deployment

    def flatten(self, deployment_id: str) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.FLATTENING)
        self._flatten_scope(deployment_id=deployment_id)
        if deployment.state is D.FLATTENING and not self._positions.positions(deployment_id=deployment_id):
            self._transition(deployment, D.STOPPED, "flattened")
        return deployment

    def fail(self, deployment_id: str, reason: str) -> Deployment:
        """Unrecoverable strategy error; open orders are canceled per the default stop policy."""
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.FAILED, reason)
        self.oms.cancel_all(deployment_id=deployment_id)
        return deployment

    def retire(self, deployment_id: str) -> Deployment:
        deployment = self.get_deployment(deployment_id)
        self._transition(deployment, D.RETIRED)
        return deployment

    def _transition(self, deployment: Deployment, target: DeploymentState, reason: str | None = None) -> None:
        DEPLOYMENT_STATE_MACHINE.assert_transition(deployment.state, target)
        previous = deployment.state
        deployment.history.append((previous, target, self._clock.now(), reason))
        deployment.state, deployment.state_reason = target, reason
        self._bus.publish(
            "deployment.state.changed",
            {
                "deployment_id": deployment.deployment_id,
                "from": previous.value,
                "to": target.value,
                "reason": reason,
            },
            producer="lte",
            partition_key=deployment.account_id,
        )

    # ---- kill switches ----------------------------------------------------------------------

    def trigger_kill_switch(
        self,
        scope: KillSwitchScope,
        action: KillSwitchAction,
        *,
        reason: str,
        actor: str,
        target_id: str | None = None,
    ) -> KillSwitch:
        if scope is not KillSwitchScope.GLOBAL and not target_id:
            raise PlatformError("KILL_SWITCH_TARGET_REQUIRED", "target_id is required for non-global scope")
        switch = KillSwitch(self._ids.next("KS"), scope, target_id, action, reason, actor, self._clock.now())
        self.kill_switches[switch.kill_switch_id] = switch
        self._bus.publish(
            "killswitch.triggered",
            {
                "kill_switch_id": switch.kill_switch_id,
                "scope": scope.value,
                "target_id": target_id,
                "action": action.value,
                "reason": reason,
                "actor": actor,
            },
            producer="lte",
        )
        for deployment in self.deployments.values():
            in_scope = switch.covers(
                account_id=deployment.account_id, deployment_id=deployment.deployment_id, instrument_id=None
            ) or (scope is KillSwitchScope.INSTRUMENT and target_id in deployment.instruments)
            if in_scope and DEPLOYMENT_STATE_MACHINE.can_transition(deployment.state, D.HALTED):
                self._transition(deployment, D.HALTED, f"kill switch {switch.kill_switch_id}: {reason}")

        if action in (KillSwitchAction.CANCEL_OPEN, KillSwitchAction.FLATTEN):
            for order in self.oms.list_orders(working_only=True):
                if switch.covers(
                    account_id=order.account_id,
                    deployment_id=order.deployment_id,
                    instrument_id=order.instrument_id,
                ):
                    try:
                        self.oms.cancel(order.order_id)
                    except PlatformError:
                        # e.g. SUBMITTED or UNKNOWN: cannot be canceled yet; reported (FR-19042).
                        switch.uncancelable_order_ids.append(order.order_id)
        if action is KillSwitchAction.FLATTEN:
            self._flatten_scope(switch=switch)
        return switch

    def release_kill_switch(self, kill_switch_id: str, *, actor: str, reason: str) -> KillSwitch:
        if not reason.strip():
            raise PlatformError("REASON_REQUIRED", "a reason is required to release a kill switch")
        switch = self.kill_switches.get(kill_switch_id)
        if switch is None:
            raise NotFoundError("KILL_SWITCH_NOT_FOUND", f"unknown kill switch {kill_switch_id}")
        if not switch.active:
            raise PlatformError("KILL_SWITCH_NOT_ACTIVE", "kill switch already released")
        switch.released_by, switch.released_at, switch.release_reason = actor, self._clock.now(), reason
        self._bus.publish(
            "killswitch.released",
            {"kill_switch_id": kill_switch_id, "actor": actor, "reason": reason},
            producer="lte",
        )
        return switch

    def _covering_switches(self, account_id: str, deployment_id: str, instrument_id: str | None):
        return (
            s
            for s in self.kill_switches.values()
            if s.active
            and s.covers(account_id=account_id, deployment_id=deployment_id, instrument_id=instrument_id)
        )

    def _flatten_scope(self, *, deployment_id: str | None = None, switch: KillSwitch | None = None) -> None:
        for position in self._positions.positions():
            if deployment_id is not None and position.deployment_id != deployment_id:
                continue
            if switch is not None and not switch.covers(
                account_id=position.account_id,
                deployment_id=position.deployment_id,
                instrument_id=position.instrument_id,
            ):
                continue
            self.oms.submit(
                OrderRequest(
                    account_id=position.account_id,
                    instrument_id=position.instrument_id,
                    side=Side.SELL if position.quantity > 0 else Side.BUY,
                    order_type=OrderType.MARKET,
                    quantity=abs(position.quantity),
                    deployment_id=position.deployment_id,
                    reduce_only=True,
                    source=OrderSource.SYSTEM,
                    submitter="system:flatten",
                )
            )

    def _on_position_updated(self, event: Event) -> None:
        deployment = self.deployments.get(event.payload["deployment_id"])
        if (
            deployment
            and deployment.state is D.FLATTENING
            and not self._positions.positions(deployment_id=deployment.deployment_id)
        ):
            self._transition(deployment, D.STOPPED, "flattened")

    def _on_risk_breach(self, event: Event) -> None:
        action = _KILL_SWITCH_BY_RISK_ACTION.get(event.payload.get("action", ""))
        if action is not None:
            self.trigger_kill_switch(
                KillSwitchScope.ACCOUNT,
                action,
                target_id=event.payload["account_id"],
                reason=f"risk breach {event.payload['limit_type']}",
                actor="rms",
            )

    # ---- maintenance & eligibility ----------------------------------------------------------

    def set_maintenance_mode(self, enabled: bool) -> None:
        self.maintenance_mode = enabled
        self._bus.publish("maintenance.mode.changed", {"enabled": enabled}, producer="lte")

    def check_eligibility(self, order: Order) -> str | None:
        """Return a blocking reason code, or None when the order may proceed (BR-19-02)."""
        account = self.accounts.get(order.account_id)
        if account is None:
            return "ACCOUNT_NOT_FOUND"
        system_reducing = order.source is OrderSource.SYSTEM and order.reduce_only
        if system_reducing:
            return None
        if account.status is not AccountStatus.ACTIVE:
            return "ACCOUNT_NOT_ACTIVE"
        if self.maintenance_mode:
            return "MAINTENANCE_MODE"
        if any(self._covering_switches(order.account_id, order.deployment_id, order.instrument_id)):
            return "KILL_SWITCH_ACTIVE"
        if order.source is OrderSource.STRATEGY:
            deployment = self.deployments.get(order.deployment_id)
            if deployment is None or deployment.state is not D.RUNNING:
                return "DEPLOYMENT_NOT_RUNNING"
            if order.instrument_id not in deployment.instruments:
                return "INSTRUMENT_NOT_IN_UNIVERSE"
        return None
