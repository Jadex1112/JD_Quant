"""Application wiring and settings."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jdquant.ai.analyst import analyst_for, chat_from_env
from jdquant.ai.copilot import Copilot
from jdquant.ai.copilot_tools import build_tools
from jdquant.ai.models import ModelRegistry
from jdquant.autopilot.engine import Autopilot
from jdquant.autopilot.lab import StrategyLab
from jdquant.autopilot.monitor import TradeMonitor
from jdquant.connectivity.connections import ConnectionManager, HttpFactory
from jdquant.connectivity.feeds.manager import FeedManager
from jdquant.connectivity.poller import VenuePoller
from jdquant.intelligence.service import MarketIntelligence
from jdquant.marketdata.live import LiveMarket, SimulatedFeed
from jdquant.marketdata.recorder import MarketStore
from jdquant.persistence.store import Store
from jdquant.platform import Platform, build_paper_platform
from jdquant.security.audit import AuditLog
from jdquant.security.identity import IdentityService
from jdquant.security.secrets import SecretBox, SecretStore, load_master_key
from jdquant.strategy.runner import DeploymentRunner

log = logging.getLogger(__name__)


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.lower() in ("1", "true", "yes")


@dataclass
class Settings:
    data_dir: Path | None = None
    cookie_secure: bool = False
    enforce_mfa_for_privileged: bool = True
    allow_setup: bool = True
    background_polling: bool = False
    poll_interval: float = 2.0
    demo_feed: bool = False  # simulated random-walk prices for instruments without a live source
    web_dir: Path | None = None  # built web UI; defaults to the bundled jdquant/web_dist

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = os.environ.get("JDQ_DATA_DIR", "data")
        return cls(
            data_dir=Path(data_dir) if data_dir != ":memory:" else None,
            cookie_secure=_flag("JDQ_COOKIE_SECURE", False),
            enforce_mfa_for_privileged=_flag("JDQ_ENFORCE_MFA", True),
            allow_setup=_flag("JDQ_ALLOW_SETUP", True),
            background_polling=_flag("JDQ_BACKGROUND_POLLING", True),
            poll_interval=float(os.environ.get("JDQ_POLL_INTERVAL", "2")),
            demo_feed=_flag("JDQ_DEMO_FEED", False),
            web_dir=Path(os.environ["JDQ_WEB_DIR"]) if os.environ.get("JDQ_WEB_DIR") else None,
        )


@dataclass
class AppContext:
    platform: Platform
    store: Store
    audit: AuditLog
    identity: IdentityService
    secrets: SecretStore
    settings: Settings
    services: dict[str, Any] = field(default_factory=dict)


def build_context(
    settings: Settings | None = None,
    platform: Platform | None = None,
    *,
    http_factory: HttpFactory | None = None,
    llm_client_factory: Callable[[], Any] | None = None,
) -> AppContext:
    settings = settings or Settings.from_env()
    box = SecretBox(load_master_key(settings.data_dir))
    holder: dict[str, ConnectionManager] = {}

    def attach_connections(p: Platform) -> None:
        store_ = p.store or Store(":memory:")
        manager = ConnectionManager(p, store_, SecretStore(store_, box), http_factory)
        manager.load_all()
        holder["connections"] = manager

    if platform is None:
        store = Store(settings.data_dir / "jdquant.db") if settings.data_dir else Store(":memory:")
        platform = build_paper_platform(store=store, before_recovery=attach_connections)
    else:
        if platform.store is None:
            platform.store = Store(":memory:")
        attach_connections(platform)
    store = platform.store
    connections = holder["connections"]
    audit = AuditLog(store, platform.clock)
    identity = IdentityService(
        store, platform.clock, audit, box, enforce_mfa_for_privileged=settings.enforce_mfa_for_privileged
    )
    runner = DeploymentRunner(platform, connections.data_source_for)
    poller = VenuePoller(platform, connections, runner, interval=settings.poll_interval)
    models = ModelRegistry(store, platform.clock, platform.bus, single_user=platform.trading.single_user)
    runner.models = models
    with platform.lock:
        runner.sync_all()  # resume RUNNING/PAUSED deployments after a restart (FR-19021)
    live = LiveMarket()
    platform.market.listeners.append(live.on_quote)
    poller.watched = live.watched
    services = {
        "connections": connections,
        "runner": runner,
        "poller": poller,
        "models": models,
        "live": live,
    }
    if settings.demo_feed:

        def has_source(instrument_id: str) -> bool:
            intelligence = services.get("intelligence")
            return connections.data_source_for(instrument_id) is not None or bool(
                intelligence and intelligence.simulated_covers(instrument_id)
            )

        feed = SimulatedFeed(platform, poller.on_quote, has_source=has_source, live=live)
        feed.backfill(live)
        services["demo_feed"] = feed
    context = AppContext(platform, store, audit, identity, SecretStore(store, box), settings, services)
    services["copilot"] = Copilot(
        store, platform.clock, audit, build_tools(context), client_factory=llm_client_factory
    )
    chat = chat_from_env(services["copilot"], services["copilot"].calls)
    services["autopilot"] = Autopilot(
        platform,
        store,
        audit,
        runner=runner,
        models=models,
        venue_candles=_venue_candles(connections),
        live_ready=lambda account_id: bool(
            (adapter := connections.adapter_for_account(account_id)) and adapter.is_ready()
        ),
        analyst=analyst_for(chat) if chat is not None else None,
    )
    monitor = TradeMonitor(services["autopilot"], chat, data_source=connections.data_source_for, live=live)
    services["monitor"] = monitor
    services["lab"] = StrategyLab(platform, store, services["autopilot"], chat, runner)
    market_store = (
        MarketStore(settings.data_dir / "market.db") if settings.data_dir else MarketStore(":memory:")
    )
    intelligence = MarketIntelligence(
        platform,
        store,
        market_store,
        connections=connections,
        live=live,
        chat=chat,
        forward_quote=poller.on_quote,
        simulated_depth=settings.demo_feed,
    )
    services["intelligence"] = intelligence
    intelligence.feeds = FeedManager(connections, intelligence.hub, platform.clock)
    _attach_automation(platform, store, services, connections, runner, intelligence)
    poller.hub = intelligence.hub
    poller.depth_watched = intelligence.depth_watched
    poller.streamed = intelligence.streamed
    poller.latency = intelligence.record_rest
    runner.entry_gate = monitor.gate
    for hosted in runner.hosted.values():  # deployments resumed before the monitor existed
        hosted.host.ctx.entry_gate = monitor.gate
    return context


def _venue_candles(connections: ConnectionManager):
    def load(instrument, interval_seconds: int, limit: int):
        source = connections.data_source_for(instrument.instrument_id)
        if source is None:
            return None
        try:
            return source.fetch_candles(instrument, interval_seconds, limit)
        except Exception as exc:  # fall back to other sources; the cycle records which one it used
            log.warning("history for %s unavailable: %s", instrument.instrument_id, exc)
            return None

    return load


def _attach_automation(platform, store, services, connections, runner, intelligence) -> None:
    """Signals, the trade journal, execution quality, pre-trade guards and circuit breakers."""
    from decimal import Decimal

    from jdquant.risk.circuit import CircuitBreakers
    from jdquant.risk.guards import BalanceCache, TradingGuards
    from jdquant.strategy.registry import StrategyRegistry
    from jdquant.trading.execution import ExecutionMonitor
    from jdquant.trading.journal import TradeJournal
    from jdquant.trading.signals import SignalLog

    def deployment_name(deployment_id: str) -> str:
        deployment = platform.trading.deployments.get(deployment_id)
        return deployment.strategy_name if deployment else ""

    def venue_of(account_id: str) -> str:
        adapter = connections.adapter_for_account(account_id)
        return adapter.venue if adapter is not None else "PAPER"

    def multiplier(instrument_id: str) -> float:
        try:
            return float(platform.instruments.get(instrument_id).contract_multiplier)
        except Exception:
            return 1.0

    def fx(quote: str, account_currency: str) -> Decimal:
        if quote == account_currency:
            return Decimal(1)
        rates = {**services["autopilot"].fx_rates(), "INR": Decimal(1)}
        return rates.get(quote, Decimal(1)) / rates.get(account_currency, Decimal(1))

    journal = TradeJournal(
        store,
        platform.bus,
        platform.instruments,
        context=intelligence.trade_context,
        deployment_name=deployment_name,
    )
    signals = SignalLog(store, platform.bus, platform.risk, deployment_name=deployment_name)
    execution = ExecutionMonitor(
        store,
        platform.bus,
        platform.market,
        book=intelligence.hub.book,
        venue_of=venue_of,
        multiplier=multiplier,
    )

    def broker_problem(account_id: str) -> str | None:
        adapter = connections.adapter_for_account(account_id)
        if adapter is None:
            return "BROKER_NOT_CONNECTED"
        if not adapter.is_ready():
            return "BROKER_NOT_SIGNED_IN"
        if adapter.circuit_open_until > time.monotonic():
            return "BROKER_UNAVAILABLE"
        return None

    def margin_rate(order) -> Decimal:
        from jdquant.autopilot.research import market_leverage
        from jdquant.markets.india import Product

        adapter = connections.adapter_for_account(order.account_id)
        intraday = getattr(adapter, "product", None) is Product.INTRADAY
        cap, _ = market_leverage(platform.instruments.get(order.instrument_id), intraday)
        return 1 / cap

    balances = BalanceCache(platform, connections)
    guards = TradingGuards(
        store,
        platform,
        expected=execution.expected,
        corporate=intelligence.news.upcoming_for,
        corporate_mode=lambda: intelligence.news.block_mode,
        fx_to_account=fx,
        broker_problem=broker_problem,
        free_funds=balances.free,
        margin_rate=margin_rate,
    )
    platform.trading.extra_checks.append(guards)
    circuit = CircuitBreakers(platform, store, connections=connections, journal=journal, execution=execution)
    intelligence.periodic += [
        ("circuit", 10, circuit.tick),
        ("reconcile", 60, circuit.reconcile),
        ("balances", 60, balances.refresh),
    ]
    runner.features = intelligence.features
    services.update(
        journal=journal,
        signals=signals,
        execution=execution,
        guards=guards,
        circuit=circuit,
        balances=balances,
    )
    services["forecasts"] = _forecast_service(platform, store, services)
    runner.forecast = lambda iid, candles, interval, horizon: services["forecasts"].forecast_candles(
        iid, candles, interval_seconds=interval, horizon=horizon
    )
    intelligence.periodic.append(("forecast-score", 300, services["forecasts"].score_due))
    from jdquant.ai.research_desk import ResearchDesk

    services["desk"] = ResearchDesk(
        platform,
        store,
        chat=lambda: services["lab"].chat,
        history=_history_source(platform, services),
        intelligence=intelligence,
        forecasts=services["forecasts"],
    )
    intelligence.periodic.append(("desk-score", 600, services["desk"].score_due))
    services["registry"] = StrategyRegistry(
        store, platform, candles=services["autopilot"]._venue_candles, journal=journal, runner=runner
    )


def _forecast_service(platform, store, services):
    """Kronos forecasts from broker history."""
    from jdquant.forecast.service import ForecastService

    return ForecastService(platform, store, history=_history_source(platform, services))


def _history_source(platform, services):
    """Broker candles for forecasts and the research desk; synthetic (labelled) only in demo mode."""
    from datetime import timedelta
    from decimal import Decimal

    from jdquant.core.errors import PlatformError
    from jdquant.marketdata.synthetic import random_walk_candles

    def history(instrument, interval_seconds, bars):
        candles = services["autopilot"]._venue_candles(instrument, interval_seconds, bars)
        if candles:
            return candles, "broker history"
        if services.get("demo_feed") is None:
            raise PlatformError(
                "HISTORY_UNAVAILABLE",
                f"connect {instrument.venue}'s broker for {instrument.instrument_id} history",
            )
        ref = platform.market.reference_price(instrument.instrument_id) or Decimal(100)
        start = platform.clock.now() - timedelta(seconds=interval_seconds * bars)
        candles = random_walk_candles(
            instrument, start, bars, interval_seconds=interval_seconds, start_price=ref
        )
        return candles, "synthetic (demo; not recorded)"

    return history
