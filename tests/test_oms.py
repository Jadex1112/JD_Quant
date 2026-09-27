from datetime import timedelta
from decimal import Decimal

from conftest import BTC, advance, limit, market

from jdquant.core.clock import SimulatedClock
from jdquant.core.events import EventBus
from jdquant.core.types import Side
from jdquant.marketdata.cache import MarketDataCache
from jdquant.oms.manager import OrderManager, RiskUnavailable
from jdquant.oms.orders import ExecutionReport, Liquidity, OrderStatus, ReportType
from jdquant.platform import build_paper_platform

S = OrderStatus


def test_market_order_fills_and_updates_position(platform):
    order = platform.oms.submit(market(Side.BUY, "0.5"))
    assert order.status is S.FILLED
    assert order.average_fill_price == Decimal(50000)
    assert order.risk_decision_id is not None
    assert platform.positions.net_quantity("paper-main", BTC) == Decimal("0.5")
    assert [t.to_status for t in order.history] == [
        S.PENDING_RISK,
        S.PENDING_SUBMIT,
        S.SUBMITTED,
        S.OPEN,
        S.FILLED,
    ]


def test_idempotency_key_returns_existing_order(platform):
    """AC-21001."""
    first = platform.oms.submit(market(Side.BUY, "0.1", idempotency_key="k1", submitter="u1"))
    second = platform.oms.submit(market(Side.BUY, "0.1", idempotency_key="k1", submitter="u1"))
    assert first is second
    assert len(platform.oms.list_orders()) == 1


def test_validation_reports_every_violation(platform):
    """FR-21004, FR-21005."""
    order = platform.oms.submit(limit(Side.BUY, "0.000015", "50000.005"))
    assert order.status is S.REJECTED
    assert order.reject_code == "ORDER_VALIDATION_FAILED"
    assert "quantity" in order.reject_reason and "limit_price" in order.reject_reason


def test_rounding_mode_rounds_passively(clock):
    """FR-21006."""
    p = build_paper_platform(clock)
    oms = OrderManager(clock, p.bus, p.instruments, p.risk, p.venue, round_to_increments=True)
    p.venue.set_report_handler(oms.on_execution_report)
    order = oms.submit(limit(Side.BUY, "0.000015", "123.456"))
    assert order.quantity == Decimal("0.00001")
    assert order.limit_price == Decimal("123.45")


class _NoRisk:
    def evaluate(self, order):
        raise RiskUnavailable()


class _Router:
    def __init__(self):
        self.submitted, self.queries = [], []

    def submit(self, order):
        self.submitted.append(order)

    def cancel(self, order):
        pass

    def query(self, order):
        self.queries.append(order)


def _oms(clock, risk=None, router=None):
    p = build_paper_platform(clock)
    router = router or _Router()
    oms = OrderManager(clock, p.bus, p.instruments, risk or p.risk, router)
    MarketDataCache(clock)  # unused, keeps construction symmetric with live wiring
    return oms, router, p


def test_order_rejected_when_risk_unavailable(clock):
    """AC-21002: fail closed, nothing reaches the venue."""
    oms, router, _ = _oms(clock, risk=_NoRisk())
    order = oms.submit(market(Side.BUY, "0.1"))
    assert order.status is S.RISK_REJECTED
    assert order.reject_code == "RISK_UNAVAILABLE"
    assert router.submitted == []


def _report(order, kind, **kw):
    return ExecutionReport(kind, "PAPER", order.client_order_id, order.created_at, is_simulated=True, **kw)


def test_ack_timeout_moves_to_unknown_and_never_resubmits(clock):
    """AC-21003, FR-21046, FR-21047."""
    oms, router, p = _oms(clock)
    from conftest import set_quote

    set_quote(p, BTC, "49999", "50001")
    order = oms.submit(market(Side.BUY, "0.1"))
    assert order.status is S.SUBMITTED
    advance(clock, seconds=6)
    assert oms.check_timeouts() == [order]
    assert order.status is S.UNKNOWN
    assert router.queries == [order] and len(router.submitted) == 1

    oms.on_execution_report(_report(order, ReportType.NOT_FOUND))
    assert order.status is S.REJECTED and order.reject_code == "NOT_RECEIVED"


def test_duplicate_fills_are_applied_once_and_average_price_is_weighted(clock):
    """AC-21004, BR-21-01."""
    oms, _, p = _oms(clock)
    from conftest import set_quote

    set_quote(p, BTC, "49999", "50001")
    order = oms.submit(limit(Side.BUY, "1", "50000"))
    oms.on_execution_report(_report(order, ReportType.ACK))
    fill = dict(price=Decimal(100), quantity=Decimal("0.25"), liquidity=Liquidity.MAKER, venue_trade_id="t1")
    oms.on_execution_report(_report(order, ReportType.FILL, **fill))
    oms.on_execution_report(_report(order, ReportType.FILL, **fill))
    oms.on_execution_report(
        _report(order, ReportType.FILL, price=Decimal(200), quantity=Decimal("0.75"), venue_trade_id="t2")
    )
    assert oms.duplicate_fill_count == 1
    assert order.filled_quantity == Decimal(1)
    assert order.average_fill_price == Decimal(175)
    assert order.status is S.FILLED
    assert p.positions.net_quantity("paper-main", BTC) == Decimal(1)


def test_cancel_is_idempotent_and_racing_fill_is_applied(clock):
    """FR-21027 and fill racing cancel (21.4)."""
    oms, _, p = _oms(clock)
    from conftest import set_quote

    set_quote(p, BTC, "49999", "50001")
    order = oms.submit(limit(Side.BUY, "1", "49000"))
    oms.on_execution_report(_report(order, ReportType.ACK))
    oms.cancel(order.order_id)
    assert oms.cancel(order.order_id).status is S.PENDING_CANCEL
    oms.on_execution_report(
        _report(order, ReportType.FILL, price=Decimal(49000), quantity=Decimal(1), venue_trade_id="x")
    )
    assert order.status is S.FILLED


def test_resting_limit_order_fills_when_price_trades_through(platform):
    order = platform.oms.submit(limit(Side.BUY, "0.1", "49000"))
    assert order.status is S.OPEN
    from jdquant.marketdata.records import Trade

    platform.venue.on_trade(Trade(BTC, platform.clock.now(), Decimal(49000), Decimal(1)))
    assert order.status is S.OPEN  # touching the limit is not enough (BR-25-02)
    platform.venue.on_trade(Trade(BTC, platform.clock.now(), Decimal("48999.99"), Decimal(1)))
    assert order.status is S.FILLED and order.average_fill_price == Decimal(49000)


def test_unknown_order_reports_are_flagged(clock):
    bus = EventBus(clock)
    anomalies = []
    bus.subscribe("order.state.anomaly", anomalies.append)
    p = build_paper_platform(clock)
    oms = OrderManager(clock, bus, p.instruments, p.risk, _Router())
    oms.on_execution_report(ExecutionReport(ReportType.ACK, "PAPER", "nope", clock.now()))
    assert len(anomalies) == 1


def test_timeouts_do_not_touch_acknowledged_orders(platform):
    order = platform.oms.submit(limit(Side.BUY, "0.1", "49000"))
    platform.clock.set(platform.clock.now() + timedelta(minutes=1))
    assert platform.oms.check_timeouts() == []
    assert order.status is S.OPEN
    assert isinstance(platform.clock, SimulatedClock)
