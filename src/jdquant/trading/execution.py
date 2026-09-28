"""Execution quality: what each order was expected to get versus what it got.

When an order is created, the expected price is taken from the freshest order book (walking the visible
depth for the order's size) or, without depth, from the best bid/ask; limit orders expect their limit.
As fills arrive the average fill, slippage (positive means worse than expected) in price, basis points
and money, time to first and last fill, and the filled share are recorded. Aggregates by strategy,
broker and instrument show where execution costs money.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from jdquant.core.events import Event, EventBus
from jdquant.core.types import Side
from jdquant.oms.orders import OrderType

KIND = "execution"
MAX_RECORDS = 5000


class ExecutionMonitor:
    def __init__(
        self,
        store,
        bus: EventBus,
        market,
        *,
        book: Callable[[str], Any] | None = None,
        venue_of: Callable[[str], str] | None = None,
        multiplier: Callable[[str], float] | None = None,
    ):
        self._store = store
        self._market = market
        self._book = book or (lambda instrument_id: None)
        self.venue_of = venue_of or (lambda account_id: "")
        self.multiplier = multiplier or (lambda instrument_id: 1.0)
        self._lock = threading.Lock()
        self._count = 0
        self.listeners: list[Callable[[dict[str, Any]], None]] = []  # called when an order completes
        bus.subscribe("order.created", self._on_created)
        bus.subscribe("order.fill", self._on_fill)
        bus.subscribe("order.state.changed", self._on_state)

    def expected(self, instrument_id: str, side: Side, quantity: float) -> dict[str, Any] | None:
        """Pre-trade estimate: touch, depth-walked average and slippage for this size."""
        from jdquant.intelligence.orderbook import expected_fill

        book = self._book(instrument_id)
        if book is not None and (book.bids or book.asks):
            fill = expected_fill(book, side.value, quantity)
            if fill is not None:
                return {**fill, "source": f"{book.source} book ({len(book.bids)}x{len(book.asks)} levels)"}
        quote = self._market.quote(instrument_id)
        if quote is None:
            return None
        touch = float(quote.ask_price if side is Side.BUY else quote.bid_price)
        return {
            "touch": touch,
            "average_price": touch,
            "slippage": 0.0,
            "slippage_bps": 0.0,
            "source": "best bid/ask",
        }

    def _on_created(self, event: Event) -> None:
        order = event.payload["order"]
        estimate = None
        if order.order_type is OrderType.LIMIT and order.limit_price is not None:
            expected_price = float(order.limit_price)
            basis = "limit price"
        else:
            estimate = self.expected(order.instrument_id, order.side, float(order.quantity))
            expected_price = estimate["average_price"] if estimate else None
            basis = estimate["source"] if estimate else "no price"
        record = {
            "order_id": order.order_id,
            "account_id": order.account_id,
            "venue": self.venue_of(order.account_id),
            "deployment_id": order.deployment_id or None,
            "instrument_id": order.instrument_id,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "requested": str(order.quantity),
            "multiplier": self.multiplier(order.instrument_id),
            "created_at": order.created_at.isoformat(),
            "expected_price": expected_price,
            "touch_at_submit": estimate["touch"] if estimate else None,
            "expected_basis": basis,
            "expected_slippage_bps": estimate["slippage_bps"] if estimate else None,
            "filled": "0",
            "average_price": None,
            "first_fill_at": None,
            "last_fill_at": None,
            "status": "WORKING",
        }
        self._store.put(KIND, order.order_id, record)
        with self._lock:
            self._count += 1
            if self._count % 200 == 0:
                self._trim()

    def _on_fill(self, event: Event) -> None:
        fill, order = event.payload["fill"], event.payload["order"]
        record = self._store.get(KIND, order.order_id)
        if record is None:
            return
        record["filled"] = str(order.filled_quantity)
        record["average_price"] = (
            float(order.average_fill_price) if order.average_fill_price is not None else None
        )
        record["first_fill_at"] = record["first_fill_at"] or fill.exchange_ts.isoformat()
        record["last_fill_at"] = fill.exchange_ts.isoformat()
        self._finish(record, order)
        self._store.put(KIND, order.order_id, record)

    def _on_state(self, event: Event) -> None:
        order, target = event.payload["order"], event.payload["to"]
        if target not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "RISK_REJECTED"):
            return
        record = self._store.get(KIND, order.order_id)
        if record is None:
            return
        record["status"] = target
        self._finish(record, order)
        self._store.put(KIND, order.order_id, record)
        if target == "FILLED":
            for listener in list(self.listeners):
                try:
                    listener(record)
                except Exception:
                    pass

    @staticmethod
    def _finish(record: dict[str, Any], order) -> None:
        avg, expected = record.get("average_price"), record.get("expected_price")
        requested = float(record["requested"])
        filled = float(order.filled_quantity)
        record["fill_ratio"] = filled / requested if requested else None
        if avg is not None and expected:
            sign = 1 if record["side"] == "BUY" else -1
            slip = (avg - expected) * sign
            record["slippage"] = slip
            record["slippage_bps"] = slip / expected * 10_000
            record["slippage_cost"] = slip * filled * record.get("multiplier", 1.0)
        if record.get("first_fill_at"):
            start = datetime.fromisoformat(record["created_at"])
            record["seconds_to_first_fill"] = (
                datetime.fromisoformat(record["first_fill_at"]) - start
            ).total_seconds()
            record["seconds_to_complete"] = (
                datetime.fromisoformat(record["last_fill_at"]) - start
            ).total_seconds()

    def _trim(self) -> None:
        items = self._store.all(KIND)
        if len(items) > MAX_RECORDS:
            for old in sorted(items, key=lambda r: r["created_at"])[: len(items) - MAX_RECORDS]:
                self._store.delete(KIND, old["order_id"])

    def get(self, order_id: str) -> dict[str, Any] | None:
        return self._store.get(KIND, order_id)

    def records(self, limit: int = 200, deployment_id: str | None = None) -> list[dict[str, Any]]:
        items = [
            r
            for r in self._store.all(KIND)
            if r.get("average_price") is not None
            and (deployment_id is None or r["deployment_id"] == deployment_id)
        ]
        return sorted(items, key=lambda r: r["created_at"], reverse=True)[:limit]

    def summary(self) -> dict[str, Any]:
        rows = self.records(limit=MAX_RECORDS)
        out: dict[str, Any] = {}
        for dim in ("venue", "deployment_id", "instrument_id"):
            groups: dict[str, list[dict]] = defaultdict(list)
            for r in rows:
                groups[str(r.get(dim) or "manual")].append(r)
            out[dim] = sorted(
                (
                    {
                        "group": k,
                        "orders": len(v),
                        "average_slippage_bps": _mean([x.get("slippage_bps") for x in v]),
                        "worst_slippage_bps": max((x.get("slippage_bps") or 0 for x in v), default=None),
                        "slippage_cost": sum(x.get("slippage_cost") or 0 for x in v),
                        "average_fill_ratio": _mean([x.get("fill_ratio") for x in v]),
                        "average_seconds_to_fill": _mean([x.get("seconds_to_complete") for x in v]),
                    }
                    for k, v in groups.items()
                ),
                key=lambda g: -(g["slippage_cost"] or 0),
            )
        return {"orders": len(rows), "by": out}


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None
