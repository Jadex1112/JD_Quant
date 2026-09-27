from decimal import Decimal

import pytest
from conftest import BTC, limit, market

from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.oms.orders import OrderSource, OrderStatus
from jdquant.trading.engine import (
    AccountMode,
    AccountStatus,
    DeploymentState,
    KillSwitchAction,
    KillSwitchScope,
    TradingAccount,
)

S = OrderStatus


def test_account_kill_switch_cancels_and_blocks(platform):
    """AC-19001."""
    resting = platform.oms.submit(limit(Side.BUY, "0.1", "49000"))
    switch = platform.trading.trigger_kill_switch(
        KillSwitchScope.ACCOUNT,
        KillSwitchAction.CANCEL_OPEN,
        target_id="paper-main",
        reason="drill",
        actor="risk",
    )
    assert resting.status is S.CANCELED
    blocked = platform.oms.submit(market(Side.BUY, "0.1"))
    assert blocked.status is S.RISK_REJECTED and blocked.reject_code == "KILL_SWITCH_ACTIVE"

    with pytest.raises(PlatformError):
        platform.trading.release_kill_switch(switch.kill_switch_id, actor="risk", reason=" ")
    platform.trading.release_kill_switch(switch.kill_switch_id, actor="risk", reason="drill complete")
    assert platform.oms.submit(market(Side.BUY, "0.1")).status is S.FILLED


def test_flatten_kill_switch_closes_positions_and_halts_deployments(platform):
    dep = _running_deployment(platform)
    platform.oms.submit(market(Side.BUY, "0.3", deployment_id=dep.deployment_id, source=OrderSource.STRATEGY))
    platform.trading.trigger_kill_switch(
        KillSwitchScope.GLOBAL, KillSwitchAction.FLATTEN, reason="emergency", actor="risk"
    )
    assert platform.positions.net_quantity("paper-main", BTC) == 0
    assert dep.state is DeploymentState.HALTED
    with pytest.raises(PlatformError):
        platform.trading.start(dep.deployment_id)


def test_strategy_orders_require_running_deployment(platform):
    """BR-19-02."""
    dep = _running_deployment(platform)
    platform.trading.pause(dep.deployment_id)
    order = platform.oms.submit(
        market(Side.BUY, "0.1", deployment_id=dep.deployment_id, source=OrderSource.STRATEGY)
    )
    assert order.reject_code == "DEPLOYMENT_NOT_RUNNING"
    platform.trading.resume(dep.deployment_id)
    order = platform.oms.submit(
        market(Side.BUY, "0.1", deployment_id=dep.deployment_id, source=OrderSource.STRATEGY)
    )
    assert order.status is S.FILLED


def test_deployment_flatten_and_stop(platform):
    dep = _running_deployment(platform)
    platform.oms.submit(market(Side.BUY, "0.2", deployment_id=dep.deployment_id, source=OrderSource.STRATEGY))
    platform.trading.flatten(dep.deployment_id)
    assert dep.state is DeploymentState.STOPPED
    assert platform.positions.net_quantity("paper-main", BTC) == 0


def test_live_deployment_needs_a_second_approver(platform):
    """CON-203."""
    platform.trading.single_user = False
    platform.trading.register_account(TradingAccount("live-1", "Live", "BINANCE", AccountMode.LIVE, "USDT"))
    dep = platform.trading.create_deployment(
        strategy_name="ma_crossover",
        strategy_version="1.0.0",
        account_id="live-1",
        parameters={},
        instruments=[BTC],
        created_by="alice",
    )
    with pytest.raises(PlatformError):
        platform.trading.approve(dep.deployment_id, "alice")
    platform.trading.approve(dep.deployment_id, "bob")
    assert dep.state is DeploymentState.READY


def test_suspended_account_and_maintenance_block_orders(platform):
    platform.trading.set_account_status("paper-main", AccountStatus.SUSPENDED)
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "ACCOUNT_NOT_ACTIVE"
    platform.trading.set_account_status("paper-main", AccountStatus.ACTIVE)
    platform.trading.set_maintenance_mode(True)
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "MAINTENANCE_MODE"


def test_instrument_conflict_between_deployments(platform):
    """FR-19022."""
    _running_deployment(platform)
    with pytest.raises(PlatformError) as err:
        _running_deployment(platform)
    assert err.value.code == "INSTRUMENT_CONFLICT"


def test_invalid_deployment_transition(platform):
    dep = platform.trading.create_deployment(
        strategy_name="x",
        strategy_version="1.0.0",
        account_id="paper-main",
        parameters={},
        instruments=[BTC],
        created_by="u",
    )
    with pytest.raises(PlatformError) as err:
        platform.trading.pause(dep.deployment_id)
    assert err.value.code == "INVALID_STATE_TRANSITION"


def _running_deployment(platform):
    dep = platform.trading.create_deployment(
        strategy_name="manual-test",
        strategy_version="1.0.0",
        account_id="paper-main",
        parameters={},
        instruments=[BTC],
        created_by="u",
    )
    platform.trading.approve(dep.deployment_id, "u")
    platform.trading.start(dep.deployment_id)
    assert dep.state is DeploymentState.RUNNING
    assert Decimal(0) == platform.positions.net_quantity("paper-main", BTC)
    return dep


def test_kill_switch_reports_orders_it_cannot_cancel(clock):
    """FR-19042: an unacknowledged order does not abort the kill switch."""
    from test_oms import _Router

    from jdquant.oms.manager import OrderManager
    from jdquant.platform import build_paper_platform

    p = build_paper_platform(clock)
    oms = OrderManager(
        clock, p.bus, p.instruments, p.risk, _Router(), eligibility=p.trading.check_eligibility
    )
    p.trading.attach_oms(oms)
    from conftest import set_quote

    set_quote(p, BTC, "49999", "50001")
    pending = oms.submit(market(Side.BUY, "0.1"))
    assert pending.status is S.SUBMITTED
    switch = p.trading.trigger_kill_switch(
        KillSwitchScope.GLOBAL, KillSwitchAction.CANCEL_OPEN, reason="drill", actor="risk"
    )
    assert switch.uncancelable_order_ids == [pending.order_id]
    assert oms.submit(market(Side.BUY, "0.1")).reject_code == "KILL_SWITCH_ACTIVE"


def test_fill_that_breaches_daily_loss_flattens_consistently(platform):
    """Breach detected while processing a fill escalates to a flatten kill switch (FR-19047)."""
    from conftest import set_quote

    from jdquant.risk.engine import BreachAction, LimitType, RiskLimit, RiskProfile, Scope

    platform.risk.profiles[0] = RiskProfile(
        "ws",
        Scope.WORKSPACE,
        [RiskLimit(LimitType.MAX_DAILY_LOSS, Decimal(100), BreachAction.KILL_SWITCH_FLATTEN)],
    )
    platform.oms.submit(market(Side.BUY, "1"))
    set_quote(platform, BTC, "49849", "49851")  # -150 unrealized
    trigger = platform.oms.submit(market(Side.BUY, "0.1"))
    assert trigger.status is S.FILLED
    assert any(s.active for s in platform.trading.kill_switches.values())
    assert platform.positions.net_quantity("paper-main", BTC) == 0
    assert all(o.is_terminal for o in platform.oms.list_orders())
