"""Domain → API model conversion."""

from __future__ import annotations

from jdquant.api.schemas import (
    AccountOut,
    DeploymentOut,
    KillSwitchOut,
    OrderOut,
    RiskLimitOut,
    RiskProfileIO,
)
from jdquant.connectivity.connections import markets_of
from jdquant.oms.orders import Order
from jdquant.risk.engine import RiskProfile
from jdquant.trading.engine import AccountMode, Deployment, KillSwitch, TradingAccount


def order_out(order: Order) -> OrderOut:
    return OrderOut(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        account_id=order.account_id,
        deployment_id=order.deployment_id,
        instrument_id=order.instrument_id,
        side=order.side,
        order_type=order.order_type,
        time_in_force=order.time_in_force,
        quantity=order.quantity,
        limit_price=order.limit_price,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        average_fill_price=order.average_fill_price,
        fees=order.fees,
        reject_code=order.reject_code,
        reject_reason=order.reject_reason,
        risk_decision_id=order.risk_decision_id,
        source=order.source.value,
        created_at=order.created_at,
    )


def kill_switch_out(switch: KillSwitch) -> KillSwitchOut:
    return KillSwitchOut(
        kill_switch_id=switch.kill_switch_id,
        scope=switch.scope,
        target_id=switch.target_id,
        action=switch.action,
        reason=switch.reason,
        triggered_by=switch.triggered_by,
        triggered_at=switch.triggered_at,
        active=switch.active,
        released_by=switch.released_by,
        released_at=switch.released_at,
    )


def account_out(account: TradingAccount) -> AccountOut:
    return AccountOut(
        account_id=account.account_id,
        name=account.name,
        venue=account.venue,
        mode=account.mode.value,
        base_currency=account.base_currency,
        status=account.status.value,
        markets=list(markets_of(account.venue)) if account.mode is AccountMode.LIVE else ["*"],
    )


def deployment_out(d: Deployment) -> DeploymentOut:
    return DeploymentOut(
        deployment_id=d.deployment_id,
        strategy_name=d.strategy_name,
        strategy_version=d.strategy_version,
        account_id=d.account_id,
        mode=d.mode.value,
        parameters={k: str(v) if not isinstance(v, bool | int | str) else v for k, v in d.parameters.items()},
        instruments=d.instruments,
        state=d.state.value,
        state_reason=d.state_reason,
        created_by=d.created_by,
        approved_by=d.approved_by,
    )


def risk_profile_io(p: RiskProfile) -> RiskProfileIO:
    return RiskProfileIO(
        name=p.name,
        scope=p.scope.value,
        target_id=p.target_id,
        limits=[
            RiskLimitOut(limit_type=lim.limit_type, threshold=lim.threshold, action=lim.action.value)
            for lim in p.limits
        ],
        restricted_instruments=sorted(p.restricted_instruments),
        active=p.active,
    )
