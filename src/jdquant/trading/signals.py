"""Structured signals: every automated order recorded with why it was sent and what happened to it.

A strategy states its reasons before trading (`ctx.explain(...)`): reason codes such as VWAP_RECLAIM,
POSITIVE_DELTA, VOLUME_EXPANSION, a confidence, and the planned stop and target. The resulting order
carries them as tags; this log turns each automated order into a signal record and follows it through
the pipeline:

    GENERATED -> BLOCKED (eligibility or risk said no, with the reason and the risk checks)
              -> SUBMITTED -> FILLED / CANCELLED / REJECTED (by the broker)

so "why did the bot take this trade?" and "why was this signal rejected?" have exact answers.
"""

from __future__ import annotations

import threading
from typing import Any

from jdquant.core.events import Event, EventBus
from jdquant.oms.orders import OrderSource

KIND = "signal"
MAX_SIGNALS = 5000
BLOCKED_STATES = ("RISK_REJECTED",)
TERMINAL = {"FILLED": "FILLED", "CANCELED": "CANCELLED", "EXPIRED": "EXPIRED", "REJECTED": "REJECTED"}


class SignalLog:
    def __init__(self, store, bus: EventBus, risk, *, deployment_name=None):
        self._store = store
        self._risk = risk
        self.deployment_name = deployment_name or (lambda deployment_id: "")
        self._lock = threading.Lock()
        self._count = 0
        bus.subscribe("order.created", self._on_created)
        bus.subscribe("order.state.changed", self._on_state)

    @staticmethod
    def _automated(order) -> bool:
        return bool(order.deployment_id) and order.source in (OrderSource.STRATEGY, OrderSource.SYSTEM)

    def _on_created(self, event: Event) -> None:
        order = event.payload["order"]
        if not self._automated(order):
            return
        tags = order.tags
        signal = {
            "signal_id": order.signal_id or order.order_id,
            "order_id": order.order_id,
            "at": order.created_at.isoformat(),
            "deployment_id": order.deployment_id,
            "strategy": self.deployment_name(order.deployment_id),
            "account_id": order.account_id,
            "instrument_id": order.instrument_id,
            "side": order.side.value,
            "quantity": str(order.quantity),
            "entry_type": order.order_type.value,
            "limit_price": str(order.limit_price) if order.limit_price is not None else None,
            "reduce_only": order.reduce_only,
            "reason_codes": [r for r in (tags.get("reasons") or "").split(",") if r],
            "confidence": float(tags["confidence"]) if tags.get("confidence") else None,
            "stop_loss": tags.get("stop"),
            "target": tags.get("target"),
            "exit_reason": tags.get("exit_reason"),
            "status": "GENERATED",
            "blocked_reason": None,
            "risk_checks": [],
            "average_price": None,
        }
        self._store.put(KIND, order.order_id, signal)
        with self._lock:
            self._count += 1
            if self._count % 200 == 0:
                self._trim()

    def _on_state(self, event: Event) -> None:
        order, target = event.payload["order"], event.payload["to"]
        if not self._automated(order):
            return
        signal = self._store.get(KIND, order.order_id)
        if signal is None:
            return
        if target in BLOCKED_STATES:
            signal["status"] = "BLOCKED"
            signal["blocked_reason"] = order.reject_code or event.payload.get("reason")
            decision = self._risk.decisions.get(order.risk_decision_id) if order.risk_decision_id else None
            if decision is not None:
                signal["risk_checks"] = [
                    {
                        "limit": c.limit_type.value,
                        "passed": c.passed,
                        "projected": None if c.projected is None else str(c.projected),
                        "threshold": None if c.threshold is None else str(c.threshold),
                    }
                    for c in decision.checks
                ]
        elif target == "SUBMITTED":
            signal["status"] = "SUBMITTED"
        elif target in TERMINAL:
            signal["status"] = (
                TERMINAL[target] if not (target == "CANCELED" and order.filled_quantity) else "PARTIAL"
            )
            if target == "REJECTED":
                signal["blocked_reason"] = order.reject_code or order.reject_reason
            if order.average_fill_price is not None:
                signal["average_price"] = str(order.average_fill_price)
                signal["filled_quantity"] = str(order.filled_quantity)
        else:
            return
        self._store.put(KIND, order.order_id, signal)

    def _trim(self) -> None:
        items = self._store.all(KIND)
        if len(items) > MAX_SIGNALS:
            for old in sorted(items, key=lambda s: s["at"])[: len(items) - MAX_SIGNALS]:
                self._store.delete(KIND, old["order_id"])

    def list(
        self,
        *,
        deployment_id: str | None = None,
        instrument_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        items = [
            s
            for s in self._store.all(KIND)
            if (deployment_id is None or s["deployment_id"] == deployment_id)
            and (instrument_id is None or s["instrument_id"] == instrument_id)
            and (status is None or s["status"] == status)
        ]
        return sorted(items, key=lambda s: s["at"], reverse=True)[:limit]

    def get(self, order_id: str) -> dict[str, Any] | None:
        return self._store.get(KIND, order_id)

    def summary(self) -> dict[str, Any]:
        items = self._store.all(KIND)
        by_status: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for s in items:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            if s["status"] == "BLOCKED" and s.get("blocked_reason"):
                reasons[s["blocked_reason"]] = reasons.get(s["blocked_reason"], 0) + 1
        return {"total": len(items), "by_status": by_status, "blocked_reasons": reasons}
