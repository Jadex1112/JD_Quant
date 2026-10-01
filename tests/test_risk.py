from datetime import timedelta
from decimal import Decimal

from conftest import BTC, advance, limit, market, set_quote

from jdquant.core.types import Side
from jdquant.oms.orders import OrderStatus
from jdquant.risk.engine import BreachAction, LimitType, RiskLimit, RiskProfile, Scope

S = OrderStatus


def _add(platform, *limits, scope=Scope.ACCOUNT, target="paper-main", restricted=()):
    platform.risk.profiles.append(RiskProfile("test", scope, list(limits), target, set(restricted)))


def test_position_projection_includes_working_orders(platform):
    """AC-27001."""
    _add(platform, RiskLimit(LimitType.MAX_POSITION_QUANTITY, Decimal(10)))
    set_quote(platform, BTC, "99", "101")
    assert platform.oms.submit(market(Side.BUY, "8")).status is S.FILLED
    assert platform.oms.submit(limit(Side.BUY, "1", "98")).status is S.OPEN
    rejected = platform.oms.submit(limit(Side.BUY, "2", "98"))
    assert rejected.status is S.RISK_REJECTED
    assert rejected.reject_code == "RISK_MAX_POSITION_QUANTITY"


def test_most_restrictive_limit_wins(platform):
    """FR-27002."""
    _add(platform, RiskLimit(LimitType.MAX_ORDER_QUANTITY, Decimal(5)))
    _add(platform, RiskLimit(LimitType.MAX_ORDER_QUANTITY, Decimal(1)), scope=Scope.INSTRUMENT, target=BTC)
    assert platform.oms.submit(market(Side.BUY, "2")).reject_code == "RISK_MAX_ORDER_QUANTITY"


def test_price_deviation_and_restricted_list(platform):
    assert platform.oms.submit(limit(Side.BUY, "0.1", "40000")).reject_code == "RISK_PRICE_DEVIATION"
    _add(platform, restricted=[BTC])
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "RISK_RESTRICTED_INSTRUMENT"


def test_daily_loss_breach_places_account_in_reduce_only(platform):
    """AC-27002, BR-27-03."""
    platform.risk.profiles[0] = RiskProfile(
        "ws", Scope.WORKSPACE, [RiskLimit(LimitType.MAX_DAILY_LOSS, Decimal(500))]
    )
    breaches = []
    platform.bus.subscribe("risk.breach.detected", breaches.append)
    set_quote(platform, BTC, "49999", "50001")
    platform.oms.submit(market(Side.BUY, "1"))
    set_quote(platform, BTC, "49489", "49491")  # unrealized loss of 510
    platform.oms.submit(market(Side.SELL, "0.1"))  # post-trade evaluation on fill
    assert platform.risk.daily_pnl("paper-main") < -500
    assert "paper-main" in platform.risk.reduce_only_accounts
    assert len(breaches) == 1

    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "RISK_REDUCE_ONLY"
    assert platform.oms.submit(market(Side.SELL, "0.1")).status is S.FILLED


def test_reduce_only_state_clears_at_next_day(platform):
    platform.risk.reduce_only_accounts.add("paper-main")
    advance(platform.clock, days=1)
    set_quote(platform, BTC, "49999", "50001")
    assert platform.oms.submit(market(Side.BUY, "0.1")).status is S.FILLED


def test_evaluation_error_fails_closed(platform, monkeypatch):
    """AC-27004, BR-27-01."""

    def explode(order):
        raise RuntimeError("bug")

    monkeypatch.setattr(platform.risk, "_run_checks", explode)
    order = platform.oms.submit(market(Side.BUY, "0.1"))
    assert order.status is S.RISK_REJECTED and order.reject_code == "RISK_EVALUATION_ERROR"


def test_stale_data_blocks_opening_but_not_reducing_orders(platform):
    """FR-27026."""
    platform.oms.submit(market(Side.BUY, "0.2"))
    platform.clock.set(platform.clock.now() + timedelta(seconds=30))
    assert platform.oms.submit(market(Side.BUY, "0.1")).reject_code == "RISK_STALE_DATA"
    assert platform.oms.submit(market(Side.SELL, "0.1")).status is S.FILLED


def test_order_rate_limit(platform):
    _add(platform, RiskLimit(LimitType.MAX_ORDER_RATE, Decimal(3)))
    results = [platform.oms.submit(limit(Side.BUY, "0.001", "49990")).status for _ in range(4)]
    assert results == [S.OPEN, S.OPEN, S.OPEN, S.RISK_REJECTED]
    advance(platform.clock, seconds=2)
    set_quote(platform, BTC, "49999", "50001")
    assert platform.oms.submit(limit(Side.BUY, "0.001", "49990")).status is S.OPEN


def test_breach_with_kill_switch_action_triggers_kill_switch(platform):
    """FR-19047, AC-27005."""
    platform.risk.profiles[0] = RiskProfile(
        "ws",
        Scope.WORKSPACE,
        [RiskLimit(LimitType.MAX_DAILY_LOSS, Decimal(100), BreachAction.KILL_SWITCH_FLATTEN)],
    )
    platform.oms.submit(market(Side.BUY, "1"))
    set_quote(platform, BTC, "49799", "49801")
    platform.risk.evaluate_post_trade("paper-main")
    assert any(s.active for s in platform.trading.kill_switches.values())
    assert platform.positions.net_quantity("paper-main", BTC) == 0
