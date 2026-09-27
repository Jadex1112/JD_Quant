import sqlite3
from decimal import Decimal

import pytest
from conftest import BTC, T0, limit, market, set_quote

from jdquant.core.clock import SimulatedClock
from jdquant.core.types import Side
from jdquant.marketdata.records import Trade
from jdquant.oms.orders import Order, OrderStatus
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.platform import PAPER_ACCOUNT_ID, build_paper_platform
from jdquant.risk.engine import LimitType, RiskLimit, RiskProfile, Scope
from jdquant.trading.engine import (
    AccountStatus,
    Deployment,
    DeploymentState,
    KillSwitchAction,
    KillSwitchScope,
    PlatformMode,
)

S = OrderStatus


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jdquant.db"


def _boot(db, clock):
    p = build_paper_platform(clock, store=Store(db))
    set_quote(p, BTC, "49999", "50001")
    return p


def test_codec_round_trips_domain_objects(platform):
    order = platform.oms.submit(market(Side.BUY, "0.5"))
    assert decode(Order, encode(order)) == order
    dep = platform.trading.create_deployment(
        strategy_name="s",
        strategy_version="1.0.0",
        account_id=PAPER_ACCOUNT_ID,
        parameters={"a": "1"},
        instruments=[BTC],
        created_by="u",
    )
    platform.trading.approve(dep.deployment_id, "u")
    assert decode(Deployment, encode(dep)) == dep


def test_state_survives_restart(db):
    clock = SimulatedClock(T0)
    p1 = _boot(db, clock)
    p1.oms.submit(market(Side.BUY, "0.5", idempotency_key="k1", submitter="u"))
    p1.oms.submit(market(Side.SELL, "0.2"))
    resting = p1.oms.submit(limit(Side.BUY, "0.1", "49000"))
    dep = p1.trading.create_deployment(
        strategy_name="s",
        strategy_version="1.0.0",
        account_id=PAPER_ACCOUNT_ID,
        parameters={},
        instruments=["BINANCE:ETHUSDT"],
        created_by="u",
    )
    p1.trading.approve(dep.deployment_id, "u")
    p1.trading.start(dep.deployment_id)
    switch = p1.trading.trigger_kill_switch(
        KillSwitchScope.INSTRUMENT,
        KillSwitchAction.BLOCK_NEW,
        target_id="NSE:RELIANCE",
        reason="r",
        actor="a",
    )
    p1.trading.set_account_status(PAPER_ACCOUNT_ID, AccountStatus.SUSPENDED)
    p1.trading.set_account_status(PAPER_ACCOUNT_ID, AccountStatus.ACTIVE)
    p1.store.close()

    p2 = _boot(db, clock)
    assert p2.recovery.orders == 3 and p2.recovery.fills == 2
    assert p2.trading.mode is PlatformMode.NORMAL
    pos = p2.positions.get_or_create(PAPER_ACCOUNT_ID, BTC, f"MANUAL:{PAPER_ACCOUNT_ID}")
    assert pos.quantity == Decimal("0.3")
    assert pos.average_entry_price == Decimal(50000)
    assert pos.realized_pnl == 0 and pos.fees_paid == Decimal(35)
    assert p2.trading.deployments[dep.deployment_id].state is DeploymentState.RUNNING
    assert p2.trading.kill_switches[switch.kill_switch_id].active

    # idempotency survives restart (FR-21003)
    again = p2.oms.submit(market(Side.BUY, "0.5", idempotency_key="k1", submitter="u"))
    assert again.order_id == p1.oms.list_orders()[0].order_id

    # the simulated book was rebuilt, so the resting order can still fill
    p2.venue.on_trade(Trade(BTC, clock.now(), Decimal("48999"), Decimal(1)))
    assert p2.oms.get(resting.order_id).status is S.FILLED

    # risk sees the restored position when projecting (FR-92002)
    p2.risk.set_profiles(
        [RiskProfile("ws", Scope.WORKSPACE, [RiskLimit(LimitType.MAX_POSITION_QUANTITY, Decimal("0.5"))])]
    )
    assert p2.oms.submit(market(Side.BUY, "0.2")).reject_code == "RISK_MAX_POSITION_QUANTITY"
    p2.store.close()

    p3 = _boot(db, clock)
    assert p3.risk.profiles[0].limits[0].limit_type is LimitType.MAX_POSITION_QUANTITY


def test_in_flight_orders_are_resolved_on_restart(db):
    """FR-51002 / SEQ-02: an order persisted as SUBMITTED is never blindly resent."""
    clock = SimulatedClock(T0)
    p1 = _boot(db, clock)
    order = p1.oms.submit(limit(Side.BUY, "0.1", "49000"))
    order.status = S.SUBMITTED
    p1.store.put("order", order.order_id, encode(order))
    p1.store.close()

    p2 = _boot(db, clock)
    restored = p2.oms.get(order.order_id)
    assert restored.status is S.REJECTED and restored.reject_code == "NOT_RECEIVED"
    assert any(order.order_id in a for a in p2.recovery.actions)


def test_reduce_only_state_persists_within_the_day(db):
    """FR-92004."""
    clock = SimulatedClock(T0)
    p1 = _boot(db, clock)
    p1.risk.set_profiles(
        [RiskProfile("ws", Scope.WORKSPACE, [RiskLimit(LimitType.MAX_DAILY_LOSS, Decimal(100))])]
    )
    p1.oms.submit(market(Side.BUY, "1"))
    set_quote(p1, BTC, "49799", "49801")
    p1.oms.submit(market(Side.SELL, "0.1"))
    assert PAPER_ACCOUNT_ID in p1.risk.reduce_only_accounts
    p1.store.close()

    p2 = _boot(db, clock)
    assert PAPER_ACCOUNT_ID in p2.risk.reduce_only_accounts
    assert p2.oms.submit(market(Side.BUY, "0.1")).reject_code == "RISK_REDUCE_ONLY"


def test_failed_write_aborts_the_operation(db, monkeypatch):
    """Persist-before-send: nothing reaches the venue if the journal cannot write."""
    clock = SimulatedClock(T0)
    p = _boot(db, clock)
    sent = []
    monkeypatch.setattr(p.venue, "submit", sent.append)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(p.store, "put", broken)
    with pytest.raises(sqlite3.OperationalError):
        p.oms.submit(market(Side.BUY, "0.1"))
    assert sent == []


def test_fills_and_events_are_append_only(db):
    clock = SimulatedClock(T0)
    p = _boot(db, clock)
    p.oms.submit(market(Side.BUY, "0.1"))
    with pytest.raises(sqlite3.IntegrityError):
        p.store.execute("DELETE FROM fills")
    with pytest.raises(sqlite3.IntegrityError):
        p.store.execute("UPDATE events SET event_type = 'x'")
    assert p.store.schema_version == 2


def test_orders_blocked_while_recovering(platform):
    platform.trading.mode = PlatformMode.RECOVERING
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "PLATFORM_RECOVERING"
    platform.trading.mode = PlatformMode.SAFE
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "PLATFORM_SAFE_MODE"
