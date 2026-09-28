"""Platform tools exposed to the copilot. Every handler runs with the invoking user's permissions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from jdquant.ai.copilot import CopilotTool, Effect
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import PlatformError
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.security.identity import Principal
from jdquant.trading.engine import DeploymentState, KillSwitchAction, KillSwitchScope


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


STR = {"type": "string"}


def build_tools(ctx) -> list[CopilotTool]:
    p = ctx.platform

    def locked(fn):
        def run(principal: Principal, args: dict[str, Any]):
            with p.lock:
                return fn(principal, args)

        return run

    def positions(_, args):
        rows = []
        for pos in p.positions.positions(account_id=args.get("account_id")):
            price = p.market.reference_price(pos.instrument_id)
            rows.append(
                {
                    "account_id": pos.account_id,
                    "instrument_id": pos.instrument_id,
                    "deployment_id": pos.deployment_id,
                    "quantity": pos.quantity,
                    "average_entry_price": pos.average_entry_price,
                    "realized_pnl": pos.realized_pnl,
                    "fees_paid": pos.fees_paid,
                    "unrealized_pnl": pos.unrealized_pnl(price) if price is not None else None,
                }
            )
        return rows

    def orders(_, args):
        found = p.oms.list_orders(
            account_id=args.get("account_id"), working_only=bool(args.get("working_only"))
        )
        found = sorted(found, key=lambda o: o.created_at, reverse=True)[: int(args.get("limit", 20))]
        return [
            {
                "order_id": o.order_id,
                "account_id": o.account_id,
                "instrument_id": o.instrument_id,
                "side": o.side,
                "type": o.order_type,
                "quantity": o.quantity,
                "limit_price": o.limit_price,
                "status": o.status,
                "filled_quantity": o.filled_quantity,
                "average_fill_price": o.average_fill_price,
                "reject_code": o.reject_code,
                "deployment_id": o.deployment_id,
                "created_at": o.created_at,
            }
            for o in found
        ]

    def risk_status(_, args):
        account_id = args["account_id"]
        p.trading.get_account(account_id)
        return {
            "account_id": account_id,
            "daily_pnl": p.risk.daily_pnl(account_id),
            "reduce_only": account_id in p.risk.reduce_only_accounts,
            "active_kill_switches": [
                {
                    "id": s.kill_switch_id,
                    "scope": s.scope,
                    "target": s.target_id,
                    "action": s.action,
                    "reason": s.reason,
                }
                for s in p.trading.kill_switches.values()
                if s.active
            ],
            "profiles": [
                {
                    "name": pr.name,
                    "scope": pr.scope,
                    "target": pr.target_id,
                    "limits": {lim.limit_type.value: lim.threshold for lim in pr.limits},
                }
                for pr in p.risk.profiles
            ],
        }

    def deployments(_, args):
        return [
            {
                "deployment_id": d.deployment_id,
                "strategy": d.strategy_name,
                "account_id": d.account_id,
                "instruments": d.instruments,
                "state": d.state,
                "reason": d.state_reason,
            }
            for d in p.trading.deployments.values()
        ]

    def diagnose(_, args):
        """Why is this deployment not trading? (FR-61001)"""
        d = p.trading.get_deployment(args["deployment_id"])
        account = p.trading.get_account(d.account_id)
        checks = [
            {
                "check": "deployment running",
                "ok": d.state is DeploymentState.RUNNING,
                "detail": f"state {d.state.value}" + (f": {d.state_reason}" if d.state_reason else ""),
            },
            {
                "check": "account active",
                "ok": account.status.value == "ACTIVE",
                "detail": account.status.value,
            },
            {
                "check": "platform mode",
                "ok": p.trading.mode.value == "NORMAL" and not p.trading.maintenance_mode,
                "detail": f"{p.trading.mode.value}, maintenance={p.trading.maintenance_mode}",
            },
            {
                "check": "no kill switch applies",
                "ok": not p.trading.active_kill_switches_for(d.account_id, d.deployment_id),
                "detail": "",
            },
            {
                "check": "risk not reduce-only",
                "ok": d.account_id not in p.risk.reduce_only_accounts,
                "detail": "",
            },
        ]
        for instrument_id in d.instruments:
            status = p.market.status(instrument_id)
            checks.append(
                {
                    "check": f"market data fresh for {instrument_id}",
                    "ok": status.value == "LIVE",
                    "detail": status.value,
                }
            )
        runner = ctx.services.get("runner")
        hosted = runner.hosted.get(d.deployment_id) if runner else None
        if hosted is not None:
            checks.append(
                {
                    "check": "strategy errors",
                    "ok": hosted.host.error_count == 0,
                    "detail": f"{hosted.host.error_count} callback errors",
                }
            )
        recent = sorted(p.oms.list_orders(deployment_id=d.deployment_id), key=lambda o: o.created_at)[-5:]
        return {
            "deployment_id": d.deployment_id,
            "checks": checks,
            "recent_orders": [
                {"order_id": o.order_id, "status": o.status, "reject_code": o.reject_code} for o in recent
            ],
        }

    def quote(_, args):
        instrument = p.instruments.get(args["instrument_id"])
        return {
            "instrument_id": instrument.instrument_id,
            "reference_price": p.market.reference_price(instrument.instrument_id),
            "feed_status": p.market.status(instrument.instrument_id),
        }

    def models(_, args):
        registry = ctx.services.get("models")
        if registry is None:
            return []
        return [
            {
                "model": name,
                "versions": [
                    {
                        "version": v.version,
                        "stage": v.stage,
                        "test_metrics": v.report.get("metrics", {}).get("test"),
                    }
                    for v in registry.versions(name)
                ],
            }
            for name in registry.models()
        ]

    def backtest(_, args):
        instrument = p.instruments.get(args["instrument_id"])
        bars = min(int(args.get("bars", 1000)), 5000)
        candles = random_walk_candles(
            instrument,
            datetime(2025, 1, 1, tzinfo=UTC),
            bars,
            start_price=Decimal(str(args.get("start_price", "100"))),
            seed=int(args.get("seed", 7)),
        )
        result = run_backtest(
            BacktestConfig(
                args["strategy"],
                [instrument],
                {instrument.instrument_id: candles},
                args.get("parameters") or {},
            )
        )
        return {
            "data": "synthetic random walk",
            "final_equity": result.final_equity,
            "trades": len(result.trades),
            "metrics": result.metrics,
        }

    def draft_order(_, args):
        p.instruments.get(args["instrument_id"])
        return {
            "draft": {
                k: args.get(k)
                for k in ("account_id", "instrument_id", "side", "order_type", "quantity", "limit_price")
            },
            "note": "Draft only. It opens in the order ticket; the user reviews and submits it.",
        }

    # ---- state-changing (confirmation required) -----------------------------------------------

    def pause(principal, args):
        return _dep_out(
            p.trading.pause(args["deployment_id"], reason=f"paused via copilot by {principal.email}")
        )

    def stop(_, args):
        return _dep_out(p.trading.stop(args["deployment_id"]))

    def flatten(_, args):
        return _dep_out(p.trading.flatten(args["deployment_id"]))

    def cancel(_, args):
        order = p.oms.cancel(args["order_id"])
        return {"order_id": order.order_id, "status": order.status}

    def kill_switch(principal, args):
        scope = KillSwitchScope(args["scope"])
        if scope is not KillSwitchScope.GLOBAL and not args.get("target_id"):
            raise PlatformError("KILL_SWITCH_TARGET_REQUIRED", "target_id is required unless scope is GLOBAL")
        switch = p.trading.trigger_kill_switch(
            scope,
            KillSwitchAction(args["action"]),
            reason=args["reason"],
            actor=principal.user_id,
            target_id=args.get("target_id"),
        )
        return {"kill_switch_id": switch.kill_switch_id, "scope": switch.scope, "action": switch.action}

    RO, SC = Effect.READ_ONLY, Effect.STATE_CHANGING
    from jdquant.ai.director_tools import build_director_tools

    return build_director_tools(ctx) + [
        CopilotTool(
            "get_positions",
            "Open positions with P&L, optionally for one account.",
            _obj({"account_id": STR}),
            "position:view",
            RO,
            locked(positions),
        ),
        CopilotTool(
            "get_orders",
            "Recent orders, newest first. Filter by account or to working orders only.",
            _obj({"account_id": STR, "working_only": {"type": "boolean"}, "limit": {"type": "integer"}}),
            "order:view",
            RO,
            locked(orders),
        ),
        CopilotTool(
            "get_risk_status",
            "Daily P&L, reduce-only state, active kill switches and risk limits.",
            _obj({"account_id": STR}, ["account_id"]),
            "risk.profile:view",
            RO,
            locked(risk_status),
        ),
        CopilotTool(
            "list_deployments",
            "All strategy deployments with their state.",
            _obj({}),
            "deployment:view",
            RO,
            locked(deployments),
        ),
        CopilotTool(
            "diagnose_deployment",
            "Explain why a deployment is or is not trading: each eligibility check with pass/fail.",
            _obj({"deployment_id": STR}, ["deployment_id"]),
            "deployment:view",
            RO,
            locked(diagnose),
        ),
        CopilotTool(
            "get_quote",
            "Current reference price and feed status of an instrument (e.g. BINANCE:BTCUSDT).",
            _obj({"instrument_id": STR}, ["instrument_id"]),
            "marketdata:view",
            RO,
            locked(quote),
        ),
        CopilotTool(
            "list_models",
            "Registered AI models, their versions, stages and test metrics.",
            _obj({}),
            "model:view",
            RO,
            models,
        ),
        CopilotTool(
            "run_backtest",
            "Backtest a strategy template on synthetic random-walk data for quick comparisons. Templates: "
            "ma_crossover, rsi_mean_reversion, bollinger_reversion, donchian_breakout.",
            _obj(
                {
                    "strategy": STR,
                    "instrument_id": STR,
                    "parameters": {"type": "object"},
                    "bars": {"type": "integer"},
                    "start_price": STR,
                    "seed": {"type": "integer"},
                },
                ["strategy", "instrument_id"],
            ),
            "backtest:run",
            RO,
            backtest,
        ),
        CopilotTool(
            "draft_order",
            "Prepare an order for the user to review in the order ticket. This never submits anything.",
            _obj(
                {
                    "account_id": STR,
                    "instrument_id": STR,
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
                    "quantity": STR,
                    "limit_price": STR,
                },
                ["account_id", "instrument_id", "side", "order_type", "quantity"],
            ),
            "order:view",
            RO,
            locked(draft_order),
        ),
        CopilotTool(
            "pause_deployment",
            "Pause a running deployment (stops new orders, keeps positions).",
            _obj({"deployment_id": STR}, ["deployment_id"]),
            "deployment:pause",
            SC,
            locked(pause),
            lambda a: f"Pause deployment {a.get('deployment_id')}",
        ),
        CopilotTool(
            "stop_deployment",
            "Stop a deployment and cancel its open orders.",
            _obj({"deployment_id": STR}, ["deployment_id"]),
            "deployment:stop",
            SC,
            locked(stop),
            lambda a: f"Stop deployment {a.get('deployment_id')} and cancel its open orders",
        ),
        CopilotTool(
            "flatten_deployment",
            "Close every position of a deployment with market orders.",
            _obj({"deployment_id": STR}, ["deployment_id"]),
            "deployment:flatten",
            SC,
            locked(flatten),
            lambda a: f"Flatten all positions of deployment {a.get('deployment_id')}",
        ),
        CopilotTool(
            "cancel_order",
            "Cancel one working order.",
            _obj({"order_id": STR}, ["order_id"]),
            "order:cancel",
            SC,
            locked(cancel),
            lambda a: f"Cancel order {a.get('order_id')}",
        ),
        CopilotTool(
            "trigger_kill_switch",
            "Trigger a kill switch. scope: GLOBAL, ACCOUNT, STRATEGY or INSTRUMENT; action: BLOCK_NEW, "
            "CANCEL_OPEN or FLATTEN.",
            _obj(
                {
                    "scope": {"type": "string", "enum": [s.value for s in KillSwitchScope]},
                    "action": {"type": "string", "enum": [a.value for a in KillSwitchAction]},
                    "target_id": STR,
                    "reason": STR,
                },
                ["scope", "action", "reason"],
            ),
            "killswitch:trigger",
            SC,
            locked(kill_switch),
            lambda a: (
                f"Trigger {a.get('scope')} kill switch ({a.get('action')}) "
                f"{a.get('target_id') or ''}: {a.get('reason')}".replace("  ", " ")
            ),
        ),
    ]


def _dep_out(d) -> dict[str, Any]:
    return {"deployment_id": d.deployment_id, "state": d.state, "reason": d.state_reason}
