"""Single-node (T1) paper-trading platform assembly (CON-009, Chapter 26)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from jdquant.core.clock import Clock, SystemClock
from jdquant.core.events import EventBus
from jdquant.core.ids import UuidIds
from jdquant.execution.simulator import FeeSchedule, FillTiming, SimulatedVenue
from jdquant.marketdata.cache import MarketDataCache
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentRegistry
from jdquant.oms.manager import OrderManager
from jdquant.positions.engine import PositionEngine
from jdquant.risk.engine import LimitType, RiskEngine, RiskLimit, RiskProfile, Scope
from jdquant.trading.engine import AccountMode, TradingAccount, TradingEngine

PAPER_ACCOUNT_ID = "paper-main"


def default_risk_profile() -> RiskProfile:
    """Workspace defaults required by FR-27005."""
    return RiskProfile(
        "workspace-default",
        Scope.WORKSPACE,
        [
            RiskLimit(LimitType.MAX_ORDER_NOTIONAL, Decimal(250_000)),
            RiskLimit(LimitType.PRICE_DEVIATION, Decimal("0.05")),
            RiskLimit(LimitType.MAX_OPEN_ORDERS, Decimal(200)),
            RiskLimit(LimitType.MAX_ORDER_RATE, Decimal(20)),
            RiskLimit(LimitType.MAX_DAILY_LOSS, Decimal(25_000)),
        ],
    )


def demo_instruments() -> list[Instrument]:
    return [
        Instrument(
            "BINANCE",
            "BTCUSDT",
            AssetClass.CRYPTO_SPOT,
            "BTC",
            "USDT",
            Decimal("0.01"),
            Decimal("0.00001"),
            Decimal("0.00001"),
            min_notional=Decimal(5),
        ),
        Instrument(
            "BINANCE",
            "ETHUSDT",
            AssetClass.CRYPTO_SPOT,
            "ETH",
            "USDT",
            Decimal("0.01"),
            Decimal("0.0001"),
            Decimal("0.0001"),
            min_notional=Decimal(5),
        ),
        Instrument(
            "NSE", "RELIANCE", AssetClass.EQUITY, "RELIANCE", "INR", Decimal("0.05"), Decimal(1), Decimal(1)
        ),
    ]


@dataclass
class Platform:
    clock: Clock
    bus: EventBus
    instruments: InstrumentRegistry
    market: MarketDataCache
    risk: RiskEngine
    venue: SimulatedVenue
    positions: PositionEngine
    trading: TradingEngine
    oms: OrderManager


def build_paper_platform(clock: Clock | None = None, *, single_user: bool = True) -> Platform:
    clock = clock or SystemClock()
    ids = UuidIds()
    bus = EventBus(clock)
    registry = InstrumentRegistry()
    for instrument in demo_instruments():
        registry.add(instrument)
    market = MarketDataCache(clock, bus)
    risk = RiskEngine(clock, bus, registry, market, [default_risk_profile()], ids=ids)
    venue = SimulatedVenue(
        "PAPER", clock, registry, market, fill_timing=FillTiming.IMMEDIATE, fees=FeeSchedule()
    )
    positions = PositionEngine(bus, registry)
    trading = TradingEngine(clock, bus, positions, ids=ids, single_user=single_user)
    oms = OrderManager(clock, bus, registry, risk, venue, eligibility=trading.check_eligibility, ids=ids)
    venue.set_report_handler(oms.on_execution_report)
    trading.attach_oms(oms)
    trading.register_account(
        TradingAccount(PAPER_ACCOUNT_ID, "Paper (main)", "PAPER", AccountMode.PAPER, "USDT")
    )
    return Platform(clock, bus, registry, market, risk, venue, positions, trading, oms)
