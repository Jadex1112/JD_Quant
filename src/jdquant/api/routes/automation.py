"""Automated trading oversight: bot status, signals, trade journal, execution quality, guards, breakers."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, require
from jdquant.core.errors import NotFoundError
from jdquant.core.types import Side
from jdquant.markets.india import IST
from jdquant.risk.engine import LimitType, Scope
from jdquant.security.identity import Principal
from jdquant.trading.engine import AccountMode, DeploymentState

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


def _svc(request: Request, name: str):
    return ctx(request).services[name]


def _daily_loss_limit(platform, account_id: str) -> Decimal | None:
    limits = [
        limit.threshold
        for profile in platform.risk.profiles
        if profile.active
        and (
            profile.scope is Scope.WORKSPACE
            or (profile.scope is Scope.ACCOUNT and profile.target_id == account_id)
        )
        for limit in profile.limits
        if limit.limit_type is LimitType.MAX_DAILY_LOSS
    ]
    return min(limits) if limits else None


@router.get("/status")
def status(request: Request, principal: Principal = Depends(require("deployment:view"))) -> dict[str, Any]:
    """The bot dashboard: what is running, today's results, risk used, protections tripped."""
    c = ctx(request)
    p = c.platform
    now = p.clock.now()
    start_of_day = datetime.combine(now.astimezone(IST).date(), datetime.min.time(), IST)
    journal = c.services["journal"]
    today = journal.trades(since=start_of_day, limit=10_000)
    accounts = []
    with p.lock:
        for account in p.trading.accounts.values():
            positions = p.positions.positions(account_id=account.account_id)
            unrealized = Decimal(0)
            exposure = Decimal(0)
            for pos in positions:
                price = p.market.reference_price(pos.instrument_id)
                if price is not None:
                    unrealized += pos.unrealized_pnl(price)
                    exposure += abs(pos.quantity) * price * pos.multiplier
            trades = [t for t in today if t["account_id"] == account.account_id]
            limit = _daily_loss_limit(p, account.account_id)
            daily = p.risk.daily_pnl(account.account_id)
            accounts.append(
                {
                    "account_id": account.account_id,
                    "name": account.name,
                    "mode": account.mode.value,
                    "currency": account.base_currency,
                    "open_positions": len(positions),
                    "exposure": str(exposure),
                    "unrealized_pnl": str(unrealized),
                    "realized_today": str(sum(Decimal(t["net_pnl"]) for t in trades)),
                    "trades_today": len(trades),
                    "wins_today": sum(1 for t in trades if Decimal(t["net_pnl"]) > 0),
                    "losses_today": sum(1 for t in trades if Decimal(t["net_pnl"]) < 0),
                    "daily_pnl": str(daily),
                    "daily_loss_limit": None if limit is None else str(limit),
                    "daily_loss_used_pct": float(max(Decimal(0), -daily) / limit * 100) if limit else None,
                    "reduce_only": account.account_id in p.risk.reduce_only_accounts,
                }
            )
        strategies = [
            {
                "deployment_id": d.deployment_id,
                "strategy": d.strategy_name,
                "account_id": d.account_id,
                "mode": d.mode.value,
                "state": d.state.value,
                "reason": d.state_reason,
                "instruments": d.instruments,
                "trades_today": sum(1 for t in today if t["deployment_id"] == d.deployment_id),
                "pnl_today": str(
                    sum(Decimal(t["net_pnl"]) for t in today if t["deployment_id"] == d.deployment_id)
                ),
            }
            for d in p.trading.deployments.values()
            if d.state not in (DeploymentState.RETIRED, DeploymentState.DRAFT)
        ]
        switches = [
            {
                "kill_switch_id": s.kill_switch_id,
                "scope": s.scope.value,
                "target_id": s.target_id,
                "action": s.action.value,
                "reason": s.reason,
                "by": s.triggered_by,
                "at": s.triggered_at.isoformat(),
            }
            for s in p.trading.kill_switches.values()
            if s.active
        ]
    running = [s for s in strategies if s["state"] == "RUNNING"]
    live_running = [s for s in running if s["mode"] == AccountMode.LIVE.value]
    bot = "STOPPED" if any(s["scope"] == "GLOBAL" for s in switches) else "RUNNING" if running else "IDLE"
    return {
        "at": now.isoformat(),
        "bot": bot,
        "platform_mode": p.trading.mode.value,
        "running_strategies": len(running),
        "live_strategies": len(live_running),
        "accounts": accounts,
        "strategies": strategies,
        "kill_switches": switches,
        "circuit": c.services["circuit"].status(),
        "signals": c.services["signals"].summary(),
    }


@router.get("/signals")
def signals(
    request: Request,
    deployment_id: str | None = None,
    instrument_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require("deployment:view")),
):
    return _svc(request, "signals").list(
        deployment_id=deployment_id, instrument_id=instrument_id, status=status, limit=limit
    )


@router.get("/signals/{order_id}")
def signal(order_id: str, request: Request, principal: Principal = Depends(require("deployment:view"))):
    found = _svc(request, "signals").get(order_id)
    if found is None:
        raise NotFoundError("SIGNAL_NOT_FOUND", f"no signal for order {order_id}")
    return {**found, "execution": _svc(request, "execution").get(order_id)}


@router.get("/journal")
def journal(
    request: Request,
    account_id: str | None = None,
    deployment_id: str | None = None,
    instrument_id: str | None = None,
    days: int = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=500, ge=1, le=5000),
    principal: Principal = Depends(require("position:view")),
):
    j = _svc(request, "journal")
    since = ctx(request).platform.clock.now() - timedelta(days=days)
    trades = j.trades(
        account_id=account_id,
        deployment_id=deployment_id,
        instrument_id=instrument_id,
        since=since,
        limit=limit,
    )
    return {"trades": trades, "open": j.open_trades(), "patterns": j.patterns(trades)}


@router.get("/journal/{trade_id}")
def trade(trade_id: str, request: Request, principal: Principal = Depends(require("position:view"))):
    found = _svc(request, "journal").get(trade_id)
    if found is None:
        raise NotFoundError("TRADE_NOT_FOUND", f"unknown trade {trade_id}")
    execution = _svc(request, "execution")
    return {
        **found,
        "execution": {
            "entry": [execution.get(o) for o in found["entry_orders"]],
            "exit": execution.get(found["exit_order"]),
        },
        "signals": [
            s for o in [*found["entry_orders"], found["exit_order"]] if (s := _svc(request, "signals").get(o))
        ],
    }


@router.post("/journal/{trade_id}/review")
def review(trade_id: str, request: Request, principal: Principal = Depends(require("ai.copilot:use"))):
    """AI post-trade review (a template summary when no AI model is configured)."""
    c = ctx(request)
    j = c.services["journal"]
    found = j.get(trade_id)
    if found is None:
        raise NotFoundError("TRADE_NOT_FOUND", f"unknown trade {trade_id}")
    similar = [
        t
        for t in j.trades(deployment_id=found["deployment_id"], limit=2000)
        if t["trade_id"] != trade_id
        and set(t.get("entry_reasons") or []) == set(found.get("entry_reasons") or [])
    ]
    pnls = [float(t["net_pnl"]) for t in similar]
    stats = {
        "count": len(pnls),
        "win_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
        "average_pnl": sum(pnls) / len(pnls) if pnls else None,
    }
    execution = c.services["execution"]
    payload = {
        "trade": found,
        "execution": {
            "entry": [execution.get(o) for o in found["entry_orders"]],
            "exit": execution.get(found["exit_order"]),
        },
        "similar_trades": stats,
    }
    chat = c.services["intelligence"]._chat
    result = None
    if chat is not None:
        from jdquant.ai.prompts import TRADE_REVIEW

        text = chat.ask(TRADE_REVIEW, payload, max_tokens=1500, thinking=False)
        match = re.search(r"\{.*\}", text or "", re.S)
        if match:
            try:
                result = {**json.loads(match.group(0)), "by": chat.provider}
            except ValueError:
                result = None
    if result is None:
        pnl = float(found["net_pnl"])
        verdict = "made" if pnl > 0 else "lost"
        reasons = ", ".join(found.get("entry_reasons") or ["no reasons recorded"])
        result = {
            "summary": f"{found['direction'].title()} {found['instrument_id']} {verdict} {abs(pnl):,.2f} "
            f"in {found['holding_minutes']:.0f} minutes; exit: {found['exit_reason']}.",
            "review": f"Entered on {reasons}. "
            + (
                f"Similar trades: {stats['count']}, {stats['win_rate']:.0%} won."
                if stats["count"]
                else "No similar trades yet."
            ),
            "test_next": "Compare this entry condition across more trades before changing anything.",
            "normal_outcome": None,
            "by": "template",
        }
    j.set_review(trade_id, result)
    return result


class EstimateIn(BaseModel):
    instrument_id: str
    side: Side
    quantity: float = Field(gt=0)


@router.get("/execution")
def execution_quality(
    request: Request,
    deployment_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    principal: Principal = Depends(require("order:view")),
):
    ex = _svc(request, "execution")
    return {"orders": ex.records(limit=limit, deployment_id=deployment_id), "summary": ex.summary()}


@router.post("/execution/estimate")
def estimate(body: EstimateIn, request: Request, principal: Principal = Depends(require("order:view"))):
    result = _svc(request, "execution").expected(body.instrument_id, body.side, body.quantity)
    if result is None:
        raise NotFoundError("NO_PRICE", f"no book or quote for {body.instrument_id}")
    return result


@router.get("/guards")
def guards(request: Request, principal: Principal = Depends(require("risk.profile:view"))):
    return _svc(request, "guards").status()


@router.put("/guards")
def put_guards(
    body: dict[str, Any], request: Request, principal: Principal = Depends(require("risk.profile:update"))
):
    result = _svc(request, "guards").configure(body)
    ctx(request).audit.record(actor=principal.user_id, action="automation.guards", category="RISK", data=body)
    return result


@router.get("/circuit")
def circuit(request: Request, principal: Principal = Depends(require("risk.profile:view"))):
    return _svc(request, "circuit").status()


@router.put("/circuit")
def put_circuit(
    body: dict[str, Any], request: Request, principal: Principal = Depends(require("risk.profile:update"))
):
    result = _svc(request, "circuit").configure(body)
    ctx(request).audit.record(
        actor=principal.user_id, action="automation.circuit", category="RISK", data=body
    )
    return result


@router.post("/reconcile")
def reconcile(request: Request, principal: Principal = Depends(require("position:view"))):
    return _svc(request, "circuit").reconcile()


class ReplayBacktestIn(BaseModel):
    replay_id: str
    strategy: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    capital: float = Field(default=100_000, gt=0)


@router.post("/replay-backtest")
def replay_backtest(
    body: ReplayBacktestIn, request: Request, principal: Principal = Depends(require("backtest:run"))
):
    from jdquant.intelligence.replay import replay_backtest as run

    session = ctx(request).services["intelligence"].replay.get(body.replay_id)
    return run(session, body.strategy, body.parameters, capital=body.capital)


@router.get("/portfolio")
def consolidated(
    request: Request, principal: Principal = Depends(require("position:view"))
) -> dict[str, Any]:
    """Positions across every account and broker, by instrument (each account shown separately)."""
    p = ctx(request).platform
    by_instrument: dict[str, dict[str, Any]] = defaultdict(lambda: {"accounts": [], "net": Decimal(0)})
    with p.lock:
        for pos in p.positions.positions():
            account = p.trading.accounts.get(pos.account_id)
            price = p.market.reference_price(pos.instrument_id)
            row = by_instrument[pos.instrument_id]
            row["net"] += pos.quantity
            row["accounts"].append(
                {
                    "account_id": pos.account_id,
                    "mode": account.mode.value if account else None,
                    "deployment_id": pos.deployment_id,
                    "quantity": str(pos.quantity),
                    "average_price": str(pos.average_entry_price),
                    "unrealized_pnl": str(pos.unrealized_pnl(price)) if price is not None else None,
                }
            )
    return {
        "instruments": [
            {"instrument_id": iid, "net_quantity": str(v["net"]), "accounts": v["accounts"]}
            for iid, v in sorted(by_instrument.items())
        ]
    }
