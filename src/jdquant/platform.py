"""Single-node (T1) paper-trading platform assembly (CON-009, Chapter 26)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from jdquant.connectivity.router import AccountRouter
from jdquant.core.clock import Clock, SystemClock
from jdquant.core.events import EventBus
from jdquant.core.ids import UuidIds
from jdquant.execution.simulator import FillTiming, SimulatedVenue
from jdquant.marketdata.cache import MarketDataCache
from jdquant.marketdata.instruments import AssetClass, Instrument, InstrumentRegistry
from jdquant.markets.india import MarketFees
from jdquant.oms.manager import OrderManager
from jdquant.persistence.journal import Journal
from jdquant.persistence.recovery import RecoveryReport, recover
from jdquant.persistence.store import Store
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
        # Tokenized gold: one PAXG is one troy ounce, so PAXG/USDT tracks XAU/USD.
        Instrument(
            "BINANCE",
            "PAXGUSDT",
            AssetClass.CRYPTO_SPOT,
            "PAXG",
            "USDT",
            Decimal("0.01"),
            Decimal("0.0001"),
            Decimal("0.0001"),
            min_notional=Decimal(5),
        ),
        *(fx_instrument(symbol) for symbol in DEMO_FX),
    ]


# Spot gold and the major currency pairs as quoted by forex brokers (OANDA symbols). Paper trading uses
# them before a broker is connected; a connected OANDA account replaces them with its own definitions.
DEMO_FX = ("XAU_USD", "XAG_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF")
# Rough starting prices for simulated demo quotes only; live prices come from the broker.
DEMO_PRICES = {
    "OANDA:XAU_USD": Decimal(4000),
    "OANDA:XAG_USD": Decimal(48),
    "OANDA:EUR_USD": Decimal("1.1700"),
    "OANDA:GBP_USD": Decimal("1.3500"),
    "OANDA:USD_JPY": Decimal("147.00"),
    "OANDA:AUD_USD": Decimal("0.6600"),
    "OANDA:USD_CAD": Decimal("1.3800"),
    "OANDA:USD_CHF": Decimal("0.8000"),
    "BINANCE:PAXGUSDT": Decimal(4000),
    "BINANCE:BTCUSDT": Decimal(110000),
    "BINANCE:ETHUSDT": Decimal(4000),
    "NSE:RELIANCE": Decimal(1400),
}


def fx_instrument(symbol: str) -> Instrument:
    base, quote = symbol.split("_")
    metal = base in ("XAU", "XAG")
    tick = {"XAU": "0.01", "XAG": "0.0001"}.get(base) or ("0.001" if quote == "JPY" else "0.00001")
    return Instrument(
        "OANDA",
        symbol,
        AssetClass.COMMODITY if metal else AssetClass.FX,
        base,
        quote,
        Decimal(tick),
        Decimal(1),
        Decimal(1),
        shortable=True,
    )


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
    router: AccountRouter | None = None
    store: Store | None = None
    recovery: RecoveryReport | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


def build_paper_platform(
    clock: Clock | None = None,
    *,
    single_user: bool = True,
    store: Store | None = None,
    before_recovery: Callable[[Platform], None] | None = None,
) -> Platform:
    """Assemble the platform; with a store, state is persisted and recovered on startup."""
    clock = clock or SystemClock()
    ids = UuidIds()
    bus = EventBus(clock)
    registry = InstrumentRegistry()
    for instrument in demo_instruments():
        registry.add(instrument)
    market = MarketDataCache(clock, bus)
    risk = RiskEngine(clock, bus, registry, market, [default_risk_profile()], ids=ids)
    fees = MarketFees()  # Indian charges for NSE/MCX/currency fills, flat bps elsewhere
    venue = SimulatedVenue("PAPER", clock, registry, market, fill_timing=FillTiming.IMMEDIATE, fees=fees)
    positions = PositionEngine(bus, registry)
    trading = TradingEngine(clock, bus, positions, ids=ids, single_user=single_user)
    router = AccountRouter(venue)
    oms = OrderManager(clock, bus, registry, risk, router, eligibility=trading.check_eligibility, ids=ids)
    venue.set_report_handler(oms.on_execution_report)
    trading.attach_oms(oms)
    platform = Platform(clock, bus, registry, market, risk, venue, positions, trading, oms, router, store)
    if store is not None:
        Journal(store, bus, trading, risk)
    if before_recovery is not None:
        before_recovery(platform)  # e.g. attach live venue adapters so in-flight orders reach them
    if store is not None:
        platform.recovery = recover(platform, store)
    if PAPER_ACCOUNT_ID not in trading.accounts:
        trading.register_account(
            TradingAccount(PAPER_ACCOUNT_ID, "Paper (main)", "PAPER", AccountMode.PAPER, "USDT")
        )
    return platform
