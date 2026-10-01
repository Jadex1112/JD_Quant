"""Read-only "AI director" tools: the copilot can explain what the automation did, never change it.

They answer "why did the bot take this trade?", "why was this signal rejected?", "which strategy is in
drawdown?", "where were the liquidity walls?" and "how much slippage are we paying?" from the records the
platform keeps (signals, trade journal, execution quality, market events, strategy registry). The
strategy engine decides, the risk engine can veto, and only the execution engine sends orders.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from jdquant.ai.copilot import CopilotTool, Effect
from jdquant.core.errors import NotFoundError

STR = {"type": "string"}
INT = {"type": "integer"}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def build_director_tools(ctx) -> list[CopilotTool]:
    services = ctx.services  # filled after the copilot is built, so read lazily

    def svc(name: str):
        found = services.get(name)
        if found is None:
            raise NotFoundError("SERVICE_UNAVAILABLE", f"{name} is not running")
        return found

    def events_near(instrument_id: str, at: str, minutes: int = 15) -> list[dict[str, Any]]:
        from datetime import datetime

        intelligence = services.get("intelligence")
        if intelligence is None:
            return []
        t = datetime.fromisoformat(at)
        found = intelligence.event_store.search(
            instrument_id=instrument_id, since=t - timedelta(minutes=minutes), until=t, limit=15
        )
        return [
            {"at": e.at.isoformat(), "kind": e.kind, "title": e.title, "severity": e.severity} for e in found
        ]

    def explain_trade(_, args):
        journal = svc("journal")
        trade_id = args.get("trade_id")
        if trade_id:
            trade = journal.get(trade_id)
            if trade is None:
                raise NotFoundError("TRADE_NOT_FOUND", f"unknown trade {trade_id}")
        else:
            recent = journal.trades(instrument_id=args.get("instrument_id"), limit=1)
            if not recent:
                return {"message": "no closed trades recorded yet"}
            trade = recent[0]
        signals, execution = svc("signals"), svc("execution")
        orders = [*trade.get("entry_orders", []), trade.get("exit_order")]
        return {
            "trade": trade,
            "signals": [s for s in (signals.get(o) for o in orders if o) if s],
            "execution": [r for r in (execution.get(o) for o in orders if o) if r],
            "market_events_before_entry": events_near(trade["instrument_id"], trade["opened_at"]),
            "note": "Reason codes come from the strategy's own rules; market events are context, not causes.",
        }

    def explain_signal(_, args):
        signals = svc("signals")
        order_id = args.get("order_id")
        if order_id:
            signal = signals.get(order_id)
            if signal is None:
                raise NotFoundError("SIGNAL_NOT_FOUND", f"no signal for order {order_id}")
            found = [signal]
        else:
            found = signals.list(
                deployment_id=args.get("deployment_id"),
                instrument_id=args.get("instrument_id"),
                status=args.get("status"),
                limit=int(args.get("limit", 10)),
            )
        return {"signals": found, "summary": signals.summary()}

    def strategy_drawdowns(_, args):
        journal = svc("journal")
        trades = sorted(journal.trades(limit=5000), key=lambda t: t["closed_at"])
        by: dict[str, dict[str, Any]] = {}
        for t in trades:
            key = t.get("deployment_id") or "manual"
            row = by.setdefault(
                key,
                {
                    "strategy": t.get("strategy"),
                    "equity": 0.0,
                    "peak": 0.0,
                    "max_dd": 0.0,
                    "trades": 0,
                    "losing_streak": 0,
                    "current_streak": 0,
                },
            )
            pnl = float(t["net_pnl"])
            row["equity"] += pnl
            row["peak"] = max(row["peak"], row["equity"])
            row["max_dd"] = max(row["max_dd"], row["peak"] - row["equity"])
            row["trades"] += 1
            row["current_streak"] = row["current_streak"] + 1 if pnl < 0 else 0
            row["losing_streak"] = max(row["losing_streak"], row["current_streak"])
        rows = [
            {
                "deployment_id": k,
                "strategy": v["strategy"],
                "net_pnl": v["equity"],
                "current_drawdown": v["peak"] - v["equity"],
                "max_drawdown": v["max_dd"],
                "trades": v["trades"],
                "longest_losing_streak": v["losing_streak"],
                "losses_in_a_row_now": v["current_streak"],
            }
            for k, v in by.items()
        ]
        patterns = journal.patterns()
        return {
            "strategies": sorted(rows, key=lambda r: -r["current_drawdown"]),
            "observations": patterns.get("observations", []),
        }

    def market_events(_, args):
        intelligence = svc("intelligence")
        found = intelligence.event_store.search(
            instrument_id=args.get("instrument_id"),
            text=args.get("text"),
            category=args.get("category"),
            min_severity=args.get("min_severity"),
            limit=int(args.get("limit", 20)),
        )
        return {
            "events": [e.to_dict() for e in found],
            "disclaimer": "Order-book events describe displayed quotes and trades; they do not establish "
            "manipulation or anyone's intent.",
        }

    def wall_history(_, args):
        return svc("intelligence").orderbook.walls(args["instrument_id"])

    def execution_quality(_, args):
        execution = svc("execution")
        return {
            "summary": execution.summary(),
            "recent": execution.records(
                limit=int(args.get("limit", 10)), deployment_id=args.get("deployment_id")
            ),
        }

    def market_intelligence(_, args):
        intelligence = svc("intelligence")
        return {"features": intelligence.features(args["instrument_id"]), "health": intelligence.health()}

    def bot_status(_, args):
        p = ctx.platform
        with p.lock:
            deployments = [
                {
                    "deployment_id": d.deployment_id,
                    "strategy": d.strategy_name,
                    "version": d.strategy_version,
                    "account_id": d.account_id,
                    "mode": d.mode.value,
                    "state": d.state.value,
                    "reason": d.state_reason,
                }
                for d in p.trading.deployments.values()
                if d.state.value in ("RUNNING", "PAUSED", "READY", "STARTING")
            ]
            switches = [
                {
                    "scope": s.scope.value,
                    "target_id": s.target_id,
                    "action": s.action.value,
                    "reason": s.reason,
                }
                for s in p.trading.kill_switches.values()
                if s.active
            ]
        circuit = services.get("circuit")
        return {
            "deployments": deployments,
            "active_kill_switches": switches,
            "circuit_breakers": circuit.status() if circuit is not None else None,
            "open_trades": svc("journal").open_trades(),
        }

    def strategy_pipeline(_, args):
        registry = svc("registry")
        if args.get("strategy_id"):
            return registry.view(args["strategy_id"])
        return {"strategies": registry.list(), "settings": registry.settings.to_dict()}

    RO = Effect.READ_ONLY
    return [
        CopilotTool(
            "explain_trade",
            "Why the bot took a closed trade: its entry and exit reason codes, the signal records, fills and "
            "slippage, and market events before entry. Without trade_id, the latest trade.",
            _obj({"trade_id": STR, "instrument_id": STR}),
            "order:view",
            RO,
            explain_trade,
        ),
        CopilotTool(
            "explain_signal",
            "Automated signals with reason codes, confidence, stop/target and what happened: BLOCKED signals "
            "carry the risk or guard reason. Filter by order, deployment, instrument or status.",
            _obj({"order_id": STR, "deployment_id": STR, "instrument_id": STR, "status": STR, "limit": INT}),
            "order:view",
            RO,
            explain_signal,
        ),
        CopilotTool(
            "strategy_drawdowns",
            "Per-deployment net P&L, current and maximum drawdown and losing streaks from the trade journal, "
            "plus the journal's observations about losing patterns.",
            _obj({}),
            "deployment:view",
            RO,
            strategy_drawdowns,
        ),
        CopilotTool(
            "market_events",
            "Search detected market events (walls, sweeps, absorption, VWAP, news, feed problems).",
            _obj({"instrument_id": STR, "text": STR, "category": STR, "min_severity": STR, "limit": INT}),
            "marketdata:view",
            RO,
            market_events,
        ),
        CopilotTool(
            "wall_history",
            "Liquidity walls on an instrument: active walls and how recent ones ended (consumed, cancelled).",
            _obj({"instrument_id": STR}, ["instrument_id"]),
            "marketdata:view",
            RO,
            wall_history,
        ),
        CopilotTool(
            "execution_quality",
            "Slippage, fill ratio and time to fill by venue, deployment and instrument, and recent orders.",
            _obj({"deployment_id": STR, "limit": INT}),
            "order:view",
            RO,
            execution_quality,
        ),
        CopilotTool(
            "market_intelligence",
            "Live order flow, walls, VWAP, structure and regime for an instrument, with feed health.",
            _obj({"instrument_id": STR}, ["instrument_id"]),
            "marketdata:view",
            RO,
            market_intelligence,
        ),
        CopilotTool(
            "bot_status",
            "Running strategies, open trades, active kill switches and circuit breaker trips.",
            _obj({}),
            "deployment:view",
            RO,
            bot_status,
        ),
        CopilotTool(
            "strategy_pipeline",
            "Strategy versions and their stage (DEVELOPMENT, BACKTEST, VALIDATION, PAPER, APPROVED, LIVE).",
            _obj({"strategy_id": STR}),
            "deployment:view",
            RO,
            strategy_pipeline,
        ),
    ]
