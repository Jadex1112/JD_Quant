"""Write-through persistence of trading state, driven by domain events (FR-51001, API-82003)."""

from __future__ import annotations

import json

from jdquant.core.events import Event, EventBus
from jdquant.persistence.codec import encode
from jdquant.persistence.store import Store
from jdquant.risk.engine import RiskEngine
from jdquant.trading.engine import TradingEngine

JOURNALED_EVENTS = (
    "order.*",
    "deployment.*",
    "killswitch.*",
    "account.*",
    "risk.*",
    "maintenance.*",
    "platform.*",
    "position.updated",
)


class Journal:
    """Critical subscriber: if a write fails, the operation that published the event fails."""

    def __init__(self, store: Store, bus: EventBus, trading: TradingEngine, risk: RiskEngine):
        self._store = store
        self._trading = trading
        self._risk = risk
        for pattern in JOURNALED_EVENTS:
            bus.subscribe(pattern, self._on_event, critical=True)

    def _on_event(self, event: Event) -> None:
        with self._store.transaction() as conn:
            self._apply(event)
            conn.execute(
                "INSERT INTO events(event_id, event_type, partition_key, occurred_at, payload) "
                "VALUES (?,?,?,?,?)",
                (
                    event.event_id,
                    event.event_type,
                    event.partition_key,
                    event.occurred_at.isoformat(),
                    json.dumps(encode(event.payload), separators=(",", ":")),
                ),
            )

    def _apply(self, event: Event) -> None:
        payload = event.payload
        kind = event.event_type.split(".")[0]
        if kind == "order":
            if "order" in payload:
                order = payload["order"]
                self._store.put("order", order.order_id, encode(order))
            if event.event_type == "order.fill":
                fill = payload["fill"]
                self._store.execute(
                    "INSERT INTO fills(fill_id, order_id, account_id, data) VALUES (?,?,?,?)",
                    (fill.fill_id, fill.order_id, fill.account_id, json.dumps(encode(fill))),
                )
        elif kind == "deployment":
            deployment = self._trading.deployments[payload["deployment_id"]]
            self._store.put("deployment", deployment.deployment_id, encode(deployment))
        elif kind == "killswitch":
            switch = self._trading.kill_switches[payload["kill_switch_id"]]
            self._store.put("kill_switch", switch.kill_switch_id, encode(switch))
        elif kind == "account":
            account = self._trading.accounts[payload["account_id"]]
            self._store.put("account", account.account_id, encode(account))
        elif event.event_type == "risk.profile.changed":
            self._store.put("risk", "profiles", {"profiles": encode(self._risk.profiles)})
        elif event.event_type == "risk.state.changed":
            self._store.put("risk", "state", payload)
        elif event.event_type == "maintenance.mode.changed":
            self._store.put("platform", "maintenance", {"enabled": payload["enabled"]})
