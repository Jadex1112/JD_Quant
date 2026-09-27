"""Startup recovery sequence (FR-51002, Chapter 92)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from jdquant.core.types import ZERO
from jdquant.oms.orders import Fill, Order, OrderStatus
from jdquant.persistence.codec import decode
from jdquant.persistence.store import Store
from jdquant.risk.engine import RiskProfile
from jdquant.trading.engine import Deployment, KillSwitch, PlatformMode, TradingAccount


@dataclass
class RecoveryReport:
    started_at: datetime
    finished_at: datetime | None = None
    accounts: int = 0
    orders: int = 0
    fills: int = 0
    working_orders: int = 0
    active_kill_switches: int = 0
    actions: list[str] = field(default_factory=list)


def load_orders(store: Store) -> list[Order]:
    return [decode(Order, d) for d in store.all("order")]


def load_fills(store: Store) -> list[Fill]:
    return [decode(Fill, json.loads(r["data"])) for r in store.query("SELECT data FROM fills ORDER BY seq")]


def recover(platform, store: Store) -> RecoveryReport:
    """Rebuild in-memory state from the store while new orders are blocked (RECOVERING mode)."""
    clock, trading, oms, risk, positions = (
        platform.clock,
        platform.trading,
        platform.oms,
        platform.risk,
        platform.positions,
    )
    report = RecoveryReport(started_at=clock.now())
    trading.mode = PlatformMode.RECOVERING

    accounts = [decode(TradingAccount, d) for d in store.all("account")]
    deployments = [decode(Deployment, d) for d in store.all("deployment")]
    switches = [decode(KillSwitch, d) for d in store.all("kill_switch")]
    maintenance = (store.get("platform", "maintenance") or {}).get("enabled", False)
    trading.restore(accounts, deployments, switches, maintenance)

    stored_profiles = store.get("risk", "profiles")
    if stored_profiles is not None:
        risk.profiles = [decode(RiskProfile, p) for p in stored_profiles["profiles"]]

    orders = load_orders(store)
    fills = load_fills(store)
    oms.restore(orders, fills)

    today = clock.now().date()
    realized_today: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for fill in fills:
        realized = positions.apply_fill(fill, publish=False)
        if fill.exchange_ts.date() == today:
            realized_today[fill.account_id] += realized - fill.fee
    risk.rebuild(
        [
            (p.account_id, p.instrument_id, p.deployment_id, p.quantity, p.average_entry_price, p.multiplier)
            for p in positions.positions()
        ],
        [o for o in orders if o.is_working],
        dict(realized_today),
        store.get("risk", "state"),
    )

    restore_working = getattr(platform.venue, "restore_working", None)
    acknowledged = (
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.PENDING_REPLACE,
    )
    working = [o for o in orders if o.status in acknowledged]
    if restore_working is not None:
        for order in working:
            restore_working(order)
    report.actions.extend(oms.resolve_in_flight())

    report.accounts = len(accounts)
    report.orders = len(orders)
    report.fills = len(fills)
    report.working_orders = len([o for o in oms.list_orders() if o.is_working])
    report.active_kill_switches = sum(1 for s in switches if s.active)
    trading.set_mode(PlatformMode.NORMAL, "recovery complete")
    report.finished_at = clock.now()
    return report
