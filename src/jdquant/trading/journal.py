"""The trade journal: every round trip, paper or live, manual or automated, with why it happened.

Fills are paired into round trips per account, deployment and instrument (average cost, partial exits
and reversals handled). Each trade records:

- entry and exit prices, times, quantity, P&L after charges and holding time;
- why it was entered: the strategy's reason codes on the order, the market events on the instrument in
  the minutes before, the regime and order-flow reading at entry;
- why it was closed: the exit order's reason (stop, target, trailing stop, signal reversal, time exit,
  manual, kill switch...);
- execution quality of both legs.

Patterns across trades (by strategy, reason, instrument, time of day, regime, direction) are summarised
so recurring strengths and weaknesses stand out. The AI can write a post-trade review; it analyses, it
does not decide trades.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.core.events import Event, EventBus
from jdquant.core.types import Side
from jdquant.markets.india import IST

KIND = "journal"
MAX_TRADES = 10_000


@dataclass
class _Open:
    direction: int  # +1 long, -1 short
    quantity: Decimal
    average: Decimal
    opened_at: datetime
    fees: Decimal
    order_ids: list[str]
    reasons: list[str]
    context: dict[str, Any] = field(default_factory=dict)


class TradeJournal:
    def __init__(
        self,
        store,
        bus: EventBus,
        instruments,
        *,
        context: Callable[[str, datetime], dict[str, Any]] | None = None,
        deployment_name: Callable[[str], str] | None = None,
    ):
        self._store = store
        self._instruments = instruments
        self.context = context or (lambda instrument_id, at: {})
        self.deployment_name = deployment_name or (lambda deployment_id: "")
        self._open: dict[tuple[str, str, str], _Open] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []  # called with each closed trade
        bus.subscribe("order.fill", self._on_fill)

    # ---- pairing fills --------------------------------------------------------------------------------

    def _on_fill(self, event: Event) -> None:
        fill, order = event.payload["fill"], event.payload["order"]
        closed = []
        with self._lock:
            key = (fill.account_id, fill.deployment_id or "", fill.instrument_id)
            signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
            fee = fill.fee if fill.fee_asset not in ("", None) else Decimal(0)
            reasons = [r for r in (order.tags.get("reasons") or "").split(",") if r]
            pos = self._open.get(key)
            remaining = signed
            if pos is not None and (remaining > 0) != (pos.direction > 0):
                qty = min(abs(remaining), pos.quantity)
                share = qty / pos.quantity
                closed.append(
                    self._close(key, pos, qty, fill, order, reasons, fee * qty / fill.quantity, share)
                )
                pos.quantity -= qty
                pos.fees -= pos.fees * share
                remaining += qty if remaining < 0 else -qty
                if pos.quantity == 0:
                    del self._open[key]
                    pos = None
            if remaining != 0:
                qty = abs(remaining)
                part_fee = fee * qty / fill.quantity
                if pos is None:
                    self._open[key] = _Open(
                        1 if remaining > 0 else -1,
                        qty,
                        fill.price,
                        fill.exchange_ts,
                        part_fee,
                        [order.order_id],
                        reasons,
                        self._safe_context(fill.instrument_id, fill.exchange_ts),
                    )
                else:
                    total = pos.quantity + qty
                    pos.average = (pos.average * pos.quantity + fill.price * qty) / total
                    pos.quantity, pos.fees = total, pos.fees + part_fee
                    if order.order_id not in pos.order_ids:
                        pos.order_ids.append(order.order_id)
        for trade in closed:
            self._store.put(KIND, trade["trade_id"], trade)
            for listener in list(self.listeners):
                try:
                    listener(trade)
                except Exception:
                    pass
        if closed:
            self._trim()

    def _safe_context(self, instrument_id: str, at: datetime) -> dict[str, Any]:
        try:
            return self.context(instrument_id, at) or {}
        except Exception:
            return {}

    def _close(self, key, pos: _Open, qty: Decimal, fill, order, reasons, exit_fee: Decimal, share: Decimal):
        account_id, deployment_id, instrument_id = key
        instrument = self._instruments.get(instrument_id)
        multiplier = instrument.contract_multiplier
        gross = (fill.price - pos.average) * qty * pos.direction * multiplier
        entry_fees = pos.fees * share
        fees = entry_fees + exit_fee
        notional = pos.average * qty * multiplier
        exit_reason = order.tags.get("exit_reason") or (reasons[0] if reasons else "")
        if not exit_reason:
            exit_reason = {"MANUAL": "manual", "SYSTEM": "system"}.get(order.source.value, "strategy signal")
        return {
            "trade_id": f"T-{uuid.uuid4().hex[:12]}",
            "account_id": account_id,
            "deployment_id": deployment_id or None,
            "strategy": self.deployment_name(deployment_id) if deployment_id else "manual",
            "instrument_id": instrument_id,
            "quote_asset": instrument.quote_asset,
            "direction": "LONG" if pos.direction > 0 else "SHORT",
            "quantity": str(qty),
            "entry_price": str(pos.average),
            "exit_price": str(fill.price),
            "opened_at": pos.opened_at.isoformat(),
            "closed_at": fill.exchange_ts.isoformat(),
            "holding_minutes": round((fill.exchange_ts - pos.opened_at) / timedelta(minutes=1), 2),
            "gross_pnl": str(gross),
            "fees": str(fees),
            "net_pnl": str(gross - fees),
            "return_pct": float((gross - fees) / notional * 100) if notional else None,
            "entry_orders": list(pos.order_ids),
            "exit_order": order.order_id,
            "entry_reasons": pos.reasons,
            "exit_reason": exit_reason,
            "entry_context": pos.context,
            "paper": bool(fill.is_simulated),
            "review": None,
        }

    def _trim(self) -> None:
        items = self._store.all(KIND)
        if len(items) > MAX_TRADES:
            for old in sorted(items, key=lambda d: d["closed_at"])[: len(items) - MAX_TRADES]:
                self._store.delete(KIND, old["trade_id"])

    # ---- reading ----------------------------------------------------------------------------------------

    def open_trades(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "account_id": a,
                    "deployment_id": d or None,
                    "instrument_id": i,
                    "direction": "LONG" if p.direction > 0 else "SHORT",
                    "quantity": str(p.quantity),
                    "entry_price": str(p.average),
                    "opened_at": p.opened_at.isoformat(),
                    "entry_reasons": p.reasons,
                }
                for (a, d, i), p in self._open.items()
            ]

    def trades(
        self,
        *,
        account_id: str | None = None,
        deployment_id: str | None = None,
        instrument_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        out = [
            t
            for t in self._store.all(KIND)
            if (account_id is None or t["account_id"] == account_id)
            and (deployment_id is None or t["deployment_id"] == deployment_id)
            and (instrument_id is None or t["instrument_id"] == instrument_id)
            and (since is None or t["closed_at"] >= since.isoformat())
        ]
        return sorted(out, key=lambda t: t["closed_at"], reverse=True)[:limit]

    def get(self, trade_id: str) -> dict[str, Any] | None:
        return self._store.get(KIND, trade_id)

    def set_review(self, trade_id: str, review: dict[str, Any]) -> None:
        trade = self.get(trade_id)
        if trade is not None:
            trade["review"] = review
            self._store.put(KIND, trade_id, trade)

    def patterns(self, trades: list[dict[str, Any]] | None = None, min_trades: int = 3) -> dict[str, Any]:
        """Win rate, average and total P&L by strategy, reason, instrument, hour, weekday, regime, side."""
        trades = trades if trades is not None else self.trades(limit=MAX_TRADES)
        dims: dict[str, Callable[[dict], list[str]]] = {
            "strategy": lambda t: [t.get("strategy") or "manual"],
            "entry_reason": lambda t: t.get("entry_reasons") or ["none recorded"],
            "exit_reason": lambda t: [t.get("exit_reason") or "unknown"],
            "instrument": lambda t: [t["instrument_id"]],
            "hour_ist": lambda t: [f"{datetime.fromisoformat(t['opened_at']).astimezone(IST).hour:02d}:00"],
            "weekday": lambda t: [datetime.fromisoformat(t["opened_at"]).astimezone(IST).strftime("%A")],
            "regime": lambda t: [(t.get("entry_context") or {}).get("regime") or "unknown"],
            "direction": lambda t: [t["direction"]],
        }
        result: dict[str, Any] = {}
        for name, key in dims.items():
            groups: dict[str, list[float]] = defaultdict(list)
            for t in trades:
                for k in key(t):
                    groups[k].append(float(t["net_pnl"]))
            rows = []
            for k, pnls in groups.items():
                wins = [p for p in pnls if p > 0]
                rows.append(
                    {
                        "group": k,
                        "trades": len(pnls),
                        "win_rate": len(wins) / len(pnls),
                        "total_pnl": sum(pnls),
                        "average_pnl": sum(pnls) / len(pnls),
                    }
                )
            result[name] = sorted(rows, key=lambda r: r["total_pnl"])
        pnls = [float(t["net_pnl"]) for t in trades]
        streak = worst = 0
        for p in sorted(trades, key=lambda t: t["closed_at"]):
            streak = streak + 1 if float(p["net_pnl"]) < 0 else 0
            worst = max(worst, streak)
        observations = []
        for name in ("entry_reason", "hour_ist", "regime", "strategy", "instrument"):
            rows = [r for r in result[name] if r["trades"] >= min_trades]
            if len(rows) >= 2:
                worst_group, best_group = rows[0], rows[-1]
                if worst_group["total_pnl"] < 0:
                    observations.append(
                        f"Weakest {name.replace('_', ' ')}: {worst_group['group']} "
                        f"({worst_group['trades']} trades, {worst_group['win_rate']:.0%} won, "
                        f"total {worst_group['total_pnl']:,.2f})"
                    )
                if best_group["total_pnl"] > 0:
                    observations.append(
                        f"Strongest {name.replace('_', ' ')}: {best_group['group']} "
                        f"({best_group['trades']} trades, {best_group['win_rate']:.0%} won, "
                        f"total {best_group['total_pnl']:,.2f})"
                    )
        return {
            "trades": len(trades),
            "net_pnl": sum(pnls),
            "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
            "max_consecutive_losses": worst,
            "groups": result,
            "observations": observations,
        }
