"""Event-driven, deterministic bar backtester (Chapter 25)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import groupby
from typing import Any

from jdquant.analytics.metrics import summarize
from jdquant.analytics.trades import RoundTrip, round_trips
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import ValidationError
from jdquant.core.events import Event, EventBus
from jdquant.core.ids import SequentialIds
from jdquant.core.types import ZERO
from jdquant.execution.simulator import FeeSchedule, FillTiming, SimulatedVenue
from jdquant.marketdata.cache import MarketDataCache
from jdquant.marketdata.instruments import Instrument, InstrumentRegistry
from jdquant.marketdata.records import Candle
from jdquant.markets.forex import rollover_days
from jdquant.oms.manager import OrderManager
from jdquant.oms.orders import Fill, Order
from jdquant.positions.engine import PositionEngine
from jdquant.risk.engine import RiskEngine, RiskLimit, RiskProfile, Scope
from jdquant.strategy.base import CandleHistory, Strategy
from jdquant.strategy.host import StrategyHost
from jdquant.strategy.templates import TEMPLATES
from jdquant.trading.engine import AccountMode, TradingAccount, TradingEngine

ENGINE_VERSION = "0.1.0"
ACCOUNT_ID = "backtest"

ASSUMPTIONS = (
    "bar resolution: market orders fill at the next bar open plus slippage (BR-25-03)",
    "limit orders fill at the limit price when the bar trades strictly through it (BR-25-02)",
    "no latency model; fills are timestamped at the open of the filling bar",
    "spot-style cash accounting for all instruments",
    "margin products pay overnight financing at each 17:00 New York rollover (three days on Wednesdays)",
)


@dataclass
class BacktestConfig:
    strategy: str | type[Strategy]
    instruments: list[Instrument]
    candles: dict[str, list[Candle]]
    parameters: dict[str, Any] = field(default_factory=dict)
    initial_capital: Decimal = Decimal(100_000)
    base_currency: str = "USD"
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    slippage_bps: Decimal = Decimal(1)
    risk_limits: list[RiskLimit] = field(default_factory=list)
    periods_per_year: float | None = None
    seed: int = 0
    models: Any = None  # ModelRegistry for ML strategies; backtests use the PRODUCTION version


@dataclass
class BacktestResult:
    orders: list[Order]
    fills: list[Fill]
    equity_curve: list[tuple[datetime, Decimal]]
    trades: list[RoundTrip]
    metrics: dict[str, Any]
    final_equity: Decimal
    reproducibility_hash: str
    financing: Decimal = ZERO  # overnight financing paid on margin products (forex, metals)
    assumptions: tuple[str, ...] = ASSUMPTIONS
    strategy_errors: int = 0


def _resolve_strategy(strategy: str | type[Strategy]) -> type[Strategy]:
    if isinstance(strategy, str):
        if strategy not in TEMPLATES:
            raise ValidationError(
                "STRATEGY_NOT_FOUND", [{"field": "strategy", "message": f"unknown {strategy}"}]
            )
        return TEMPLATES[strategy]
    return strategy


def _validate(config: BacktestConfig) -> list[dict[str, str]]:
    """Collect every configuration error before running (FR-25022)."""
    errors = []
    known = {i.instrument_id for i in config.instruments}
    if not config.instruments:
        errors.append({"field": "instruments", "message": "at least one instrument is required"})
    for instrument_id in known:
        if not config.candles.get(instrument_id):
            errors.append({"field": "candles", "message": f"no data for {instrument_id}"})
    for instrument_id in config.candles:
        if instrument_id not in known:
            errors.append({"field": "candles", "message": f"data for unknown instrument {instrument_id}"})
    intervals = {c.interval_seconds for series in config.candles.values() for c in series}
    if len(intervals) > 1:
        errors.append({"field": "candles", "message": "all series must share one interval"})
    if config.initial_capital <= 0:
        errors.append({"field": "initial_capital", "message": "must be positive"})
    return errors


def reproducibility_hash(config: BacktestConfig, strategy_cls: type[Strategy]) -> str:
    digest = hashlib.sha256()
    header = {
        "engine": ENGINE_VERSION,
        "strategy": f"{strategy_cls.__module__}.{strategy_cls.__qualname__}@{strategy_cls.version}",
        "parameters": {k: str(v) for k, v in sorted(config.parameters.items())},
        "capital": str(config.initial_capital),
        "fees": config.fees.fingerprint(),
        "slippage": str(config.slippage_bps),
        "risk": [(r.limit_type.value, str(r.threshold)) for r in config.risk_limits],
        "seed": config.seed,
    }
    digest.update(json.dumps(header, sort_keys=True).encode())
    for instrument_id in sorted(config.candles):
        for c in config.candles[instrument_id]:
            digest.update(
                f"{instrument_id}|{c.open_ts.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}".encode()
            )
    return digest.hexdigest()


def run_backtest(config: BacktestConfig) -> BacktestResult:
    strategy_cls = _resolve_strategy(config.strategy)
    errors = _validate(config)
    if errors:
        raise ValidationError("BACKTEST_CONFIG_INVALID", errors)

    all_candles = sorted(
        (c for series in config.candles.values() for c in series), key=lambda c: (c.open_ts, c.instrument_id)
    )
    interval = all_candles[0].interval_seconds
    ids = SequentialIds()
    clock = SimulatedClock(all_candles[0].open_ts)
    bus = EventBus(clock)
    registry = InstrumentRegistry()
    for instrument in config.instruments:
        registry.add(instrument)
    market = MarketDataCache(clock, bus, stale_after=timedelta(seconds=interval * 3))
    profile = RiskProfile("backtest", Scope.WORKSPACE, list(config.risk_limits))
    risk = RiskEngine(clock, bus, registry, market, [profile], ids=ids)
    venue = SimulatedVenue(
        "SIM",
        clock,
        registry,
        market,
        fill_timing=FillTiming.NEXT_BAR,
        fees=config.fees,
        slippage_bps=config.slippage_bps,
        session_id="backtest",
    )
    positions = PositionEngine(bus, registry)
    trading = TradingEngine(clock, bus, positions, ids=ids, single_user=True)
    oms = OrderManager(clock, bus, registry, risk, venue, eligibility=trading.check_eligibility, ids=ids)
    venue.set_report_handler(oms.on_execution_report)
    trading.attach_oms(oms)
    trading.register_account(
        TradingAccount(ACCOUNT_ID, "Backtest", "SIM", AccountMode.PAPER, config.base_currency)
    )
    deployment = trading.create_deployment(
        strategy_name=strategy_cls.name,
        strategy_version=strategy_cls.version,
        account_id=ACCOUNT_ID,
        parameters=config.parameters,
        instruments=[i.instrument_id for i in config.instruments],
        created_by="backtest",
    )
    trading.approve(deployment.deployment_id, "backtest")
    trading.start(deployment.deployment_id)

    cash = [config.initial_capital]

    def on_fill(event: Event) -> None:
        fill: Fill = event.payload["fill"]
        mult = registry.get(fill.instrument_id).contract_multiplier
        cash[0] -= fill.side.sign * fill.quantity * fill.price * mult + fill.fee

    bus.subscribe("order.fill", on_fill)
    history = CandleHistory()
    host = StrategyHost(
        strategy_cls,
        deployment,
        clock=clock,
        bus=bus,
        oms=oms,
        positions=positions,
        trading=trading,
        history=history,
        models=config.models,
        record_inference=False,
    )

    last_close: dict[str, Decimal] = {}
    equity_curve: list[tuple[datetime, Decimal]] = []
    financing = [ZERO]
    last_ts: list[datetime | None] = [None]

    def charge_financing(until: datetime) -> None:
        """Positions held across the daily rollover pay financing on their notional."""
        since, last_ts[0] = last_ts[0], until
        if since is None:
            return
        days = rollover_days(since, until)
        if not days:
            return
        for p in positions.positions():
            if p.quantity == 0 or p.instrument_id not in last_close:
                continue
            instrument = registry.get(p.instrument_id)
            rate = config.fees.financing_rate(instrument, 1 if p.quantity > 0 else -1)
            if rate:
                notional = abs(p.quantity) * last_close[p.instrument_id] * instrument.contract_multiplier
                cost = notional * rate * days / 365
                cash[0] -= cost
                financing[0] += cost

    for open_ts, group_iter in groupby(all_candles, key=lambda c: c.open_ts):
        group = list(group_iter)
        clock.set(open_ts)
        for candle in group:
            venue.on_bar(candle)
        clock.set(group[0].close_ts)
        charge_financing(group[0].close_ts)
        for candle in group:
            market.on_candle(candle)
            history.append(candle)
            last_close[candle.instrument_id] = candle.close
        for candle in group:
            host.on_bar(candle)
        equity_curve.append((clock.now(), _equity(cash[0], positions, last_close, registry)))
    host.stop()

    trades = round_trips(oms.fills, {i.instrument_id: i.contract_multiplier for i in config.instruments})
    periods = config.periods_per_year or 365 * 86400 / interval
    metrics = summarize(
        [t for t, _ in equity_curve], [v for _, v in equity_curve], periods, [t.net_pnl for t in trades]
    )
    return BacktestResult(
        orders=oms.list_orders(),
        fills=list(oms.fills),
        equity_curve=equity_curve,
        trades=trades,
        metrics=metrics,
        final_equity=equity_curve[-1][1],
        reproducibility_hash=reproducibility_hash(config, strategy_cls),
        strategy_errors=host.error_count,
        financing=financing[0],
    )


def _equity(
    cash: Decimal, positions: PositionEngine, prices: dict[str, Decimal], registry: InstrumentRegistry
) -> Decimal:
    value = cash
    holdings: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in positions.positions():
        holdings[p.instrument_id] += p.quantity
    for instrument_id, qty in holdings.items():
        value += qty * prices[instrument_id] * registry.get(instrument_id).contract_multiplier
    return value
