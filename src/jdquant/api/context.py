"""Application wiring and settings."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jdquant.ai.analyst import analyst_for, chat_from_env
from jdquant.ai.copilot import Copilot
from jdquant.ai.copilot_tools import build_tools
from jdquant.ai.models import ModelRegistry
from jdquant.autopilot.engine import Autopilot
from jdquant.autopilot.monitor import TradeMonitor
from jdquant.connectivity.connections import ConnectionManager, HttpFactory
from jdquant.connectivity.poller import VenuePoller
from jdquant.marketdata.live import LiveMarket, SimulatedFeed
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
        feed = SimulatedFeed(
            platform,
            poller.on_quote,
            has_source=lambda instrument_id: connections.data_source_for(instrument_id) is not None,
            live=live,
        )
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
