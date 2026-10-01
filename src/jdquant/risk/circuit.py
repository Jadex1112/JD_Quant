"""Automatic circuit breakers and position reconciliation.

These watch for conditions under which an automated system should stop opening trades, and trip a
kill switch (block new orders; existing positions and exits are left alone) with the reason recorded:

| Condition | Scope | Default |
|---|---|---|
| A running live strategy's prices stop updating during market hours | account | 60 s stale |
| The broker connection needs a new sign-in or keeps failing | account | immediately |
| The broker rejects orders repeatedly | account | 3 in 5 minutes |
| Broker positions disagree with the platform's (JD Quant thinks it holds more) | account | any shortfall |
| A fill far worse than expected | strategy | 3x expected and 100 bps |
| A strategy loses several trades in a row | strategy (paused) | 5 in a row |

A tripped breaker stays until someone releases the kill switch (or resumes the strategy): a machine
should not decide on its own that a broken feed or a position mismatch is fine again.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from jdquant.core.events import Event
from jdquant.markets.sessions import session_for
from jdquant.trading.engine import AccountMode, DeploymentState, KillSwitchAction, KillSwitchScope

log = logging.getLogger(__name__)
KIND = "circuit_breakers"
ACTOR = "circuit-breaker"


@dataclass
class CircuitSettings:
    enabled: bool = True
    stale_seconds: float = 60.0
    rejection_limit: int = 3
    rejection_window_minutes: float = 5.0
    slippage_multiple: float = 3.0
    slippage_floor_bps: float = 100.0
    consecutive_losses: int = 5
    reconcile: bool = True
    live_only: bool = True  # paper accounts are never tripped for feed or broker problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CircuitBreakers:
    def __init__(self, platform, store, *, connections=None, journal=None, execution=None):
        self._p = platform
        self._store = store
        self._connections = connections
        saved = store.get(KIND, "settings") or {}
        self.settings = CircuitSettings(
            **{k: v for k, v in saved.items() if k in CircuitSettings.__dataclass_fields__}
        )
        self.trips: deque = deque(maxlen=200)
        self.reconciliation: dict[str, dict[str, Any]] = {}
        self._rejections: dict[str, deque] = defaultdict(deque)
        self._losses: dict[str, int] = defaultdict(int)
        self._tripped: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        platform.bus.subscribe("order.state.changed", self._on_order_state)
        if journal is not None:
            journal.listeners.append(self._on_trade)
        if execution is not None:
            execution.listeners.append(self._on_execution)

    def configure(self, changes: dict[str, Any]) -> dict[str, Any]:
        values = {
            **self.settings.to_dict(),
            **{k: v for k, v in changes.items() if k in CircuitSettings.__dataclass_fields__},
        }
        self.settings = CircuitSettings(**values)
        self._store.put(KIND, "settings", self.settings.to_dict())
        return self.settings.to_dict()

    # ---- tripping ---------------------------------------------------------------------------------------

    def _trip(self, scope: KillSwitchScope, target_id: str, kind: str, reason: str) -> None:
        key = (kind, target_id)
        with self._lock:
            active = any(
                s.active
                and s.target_id == target_id
                and s.triggered_by == ACTOR
                and s.reason.startswith(kind)
                for s in self._p.trading.kill_switches.values()
            )
            if active or not self.settings.enabled:
                return
            self._tripped.add(key)
        with self._p.lock:
            switch = self._p.trading.trigger_kill_switch(
                scope,
                KillSwitchAction.BLOCK_NEW,
                reason=f"{kind}: {reason}",
                actor=ACTOR,
                target_id=target_id,
            )
        self.trips.append(
            {
                "at": self._p.clock.now().isoformat(),
                "kind": kind,
                "scope": scope.value,
                "target_id": target_id,
                "reason": reason,
                "kill_switch_id": switch.kill_switch_id,
            }
        )
        log.warning("circuit breaker %s tripped for %s: %s", kind, target_id, reason)

    def _live(self, account_id: str) -> bool:
        account = self._p.trading.accounts.get(account_id)
        return account is not None and (account.mode is AccountMode.LIVE or not self.settings.live_only)

    # ---- event-driven breakers --------------------------------------------------------------------------

    def _on_order_state(self, event: Event) -> None:
        order = event.payload["order"]
        if event.payload.get("to") != "REJECTED" or not self._live(order.account_id):
            return
        now = self._p.clock.now()
        window = timedelta(minutes=self.settings.rejection_window_minutes)
        times = self._rejections[order.account_id]
        times.append(now)
        while times and now - times[0] > window:
            times.popleft()
        if len(times) >= self.settings.rejection_limit:
            times.clear()
            self._trip(
                KillSwitchScope.ACCOUNT,
                order.account_id,
                "REPEATED_REJECTIONS",
                f"{self.settings.rejection_limit} orders rejected by the broker within "
                f"{self.settings.rejection_window_minutes:g} minutes "
                f"(last: {order.reject_code or order.reject_reason})",
            )

    def _on_trade(self, trade: dict[str, Any]) -> None:
        deployment_id = trade.get("deployment_id")
        if not deployment_id:
            return
        if Decimal(trade["net_pnl"]) < 0:
            self._losses[deployment_id] += 1
        else:
            self._losses[deployment_id] = 0
        limit = self.settings.consecutive_losses
        if limit and self._losses[deployment_id] >= limit:
            self._losses[deployment_id] = 0
            deployment = self._p.trading.deployments.get(deployment_id)
            if (
                deployment is not None
                and deployment.state is DeploymentState.RUNNING
                and self.settings.enabled
            ):
                with self._p.lock:
                    self._p.trading.pause(
                        deployment_id, reason=f"circuit breaker: {limit} losing trades in a row"
                    )
                self.trips.append(
                    {
                        "at": self._p.clock.now().isoformat(),
                        "kind": "CONSECUTIVE_LOSSES",
                        "scope": "STRATEGY",
                        "target_id": deployment_id,
                        "reason": f"{limit} losing trades in a row; strategy paused",
                    }
                )

    def _on_execution(self, record: dict[str, Any]) -> None:
        slip, expected = record.get("slippage_bps"), record.get("expected_slippage_bps") or 0.0
        deployment_id = record.get("deployment_id")
        if slip is None or not deployment_id:
            return
        threshold = max(
            self.settings.slippage_floor_bps, self.settings.slippage_multiple * max(expected, 0.0)
        )
        if slip > threshold:
            self._trip(
                KillSwitchScope.STRATEGY,
                deployment_id,
                "ABNORMAL_SLIPPAGE",
                f"filled {slip:.0f} bps worse than expected on {record['instrument_id']} "
                f"(limit {threshold:.0f} bps)",
            )

    # ---- periodic breakers ------------------------------------------------------------------------------

    def tick(self) -> None:
        if not self.settings.enabled:
            return
        now = self._p.clock.now()
        running = [d for d in self._p.trading.deployments.values() if d.state is DeploymentState.RUNNING]
        for deployment in running:
            if not self._live(deployment.account_id):
                continue
            for iid in deployment.instruments:
                try:
                    instrument = self._p.instruments.get(iid)
                except Exception:
                    continue
                if not session_for(instrument).is_open(now):
                    continue
                state = self._p.market._state.get(iid)
                updated = state.updated_at if state is not None else None
                if updated is None or (now - updated).total_seconds() > self.settings.stale_seconds:
                    age = (
                        "no price yet"
                        if updated is None
                        else f"no price for {(now - updated).total_seconds():.0f}s"
                    )
                    self._trip(
                        KillSwitchScope.ACCOUNT,
                        deployment.account_id,
                        "DATA_FEED_FAILURE",
                        f"{iid}: {age} while strategy {deployment.strategy_name} is running",
                    )
                    break
        if self._connections is not None:
            for connection in list(self._connections.connections.values()):
                adapter = self._connections.adapters.get(connection.connection_id)
                status = str(getattr(connection.status, "value", connection.status))
                has_running = any(d.account_id == connection.account_id for d in running)
                if not has_running or not self._live(connection.account_id):
                    continue
                if status in ("LOGIN_REQUIRED", "ERROR", "DEGRADED") or (
                    adapter is not None and not adapter.is_ready()
                ):
                    self._trip(
                        KillSwitchScope.ACCOUNT,
                        connection.account_id,
                        "BROKER_CONNECTION",
                        f"{connection.name} is {status.lower().replace('_', ' ')}"
                        + (f": {connection.last_error}" if getattr(connection, "last_error", None) else ""),
                    )

    def reconcile(self) -> dict[str, dict[str, Any]]:
        """Compare broker positions with the platform's for every live account with a connection."""
        if self._connections is None or not self.settings.reconcile:
            return self.reconciliation
        for connection in list(self._connections.connections.values()):
            adapter = self._connections.adapters.get(connection.connection_id)
            account_id = connection.account_id
            if adapter is None or not adapter.is_ready() or not self._live(account_id):
                continue
            try:
                broker = adapter.fetch_positions()
            except Exception as exc:
                self.reconciliation[account_id] = {"at": self._p.clock.now().isoformat(), "error": str(exc)}
                continue
            if broker is None:
                self.reconciliation[account_id] = {
                    "at": self._p.clock.now().isoformat(),
                    "supported": False,
                    "venue": connection.venue,
                }
                continue
            ours: dict[str, Decimal] = defaultdict(Decimal)
            for p in self._p.positions.positions(account_id=account_id):
                ours[p.instrument_id] += p.quantity
            rows, shortfall = [], []
            for iid, expected in ours.items():
                held = Decimal(str(broker.get(iid, 0)))
                if expected == 0 and held == 0:
                    continue
                state = (
                    "MATCH"
                    if held == expected
                    else "SHORTFALL"
                    if abs(held) < abs(expected) or (held > 0) != (expected > 0)
                    else "EXTRA"
                )
                rows.append(
                    {"instrument_id": iid, "platform": str(expected), "broker": str(held), "state": state}
                )
                if state == "SHORTFALL":
                    shortfall.append(f"{iid}: platform {expected}, broker {held}")
            for iid, held in broker.items():
                if iid not in ours and held:
                    rows.append(
                        {"instrument_id": iid, "platform": "0", "broker": str(held), "state": "BROKER_ONLY"}
                    )
            self.reconciliation[account_id] = {
                "at": self._p.clock.now().isoformat(),
                "supported": True,
                "venue": connection.venue,
                "rows": rows,
            }
            if shortfall:
                self._trip(
                    KillSwitchScope.ACCOUNT,
                    account_id,
                    "POSITION_MISMATCH",
                    "broker holds less than JD Quant expects: " + "; ".join(shortfall[:5]),
                )
        return self.reconciliation

    def status(self) -> dict[str, Any]:
        return {
            "settings": self.settings.to_dict(),
            "trips": list(self.trips)[-100:],
            "reconciliation": self.reconciliation,
            "consecutive_losses": dict(self._losses),
            "active": [
                {
                    "kill_switch_id": s.kill_switch_id,
                    "scope": s.scope.value,
                    "target_id": s.target_id,
                    "reason": s.reason,
                    "at": s.triggered_at.isoformat(),
                }
                for s in self._p.trading.kill_switches.values()
                if s.active and s.triggered_by == ACTOR
            ],
        }
