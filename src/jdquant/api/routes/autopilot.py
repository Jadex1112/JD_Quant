"""The AI autopilot: configuration, research cycles, decisions and live arming."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, locked, require
from jdquant.autopilot.engine import Autopilot
from jdquant.core.errors import NotFoundError
from jdquant.persistence.codec import encode
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["autopilot"])


class ArmIn(BaseModel):
    account_id: str
    capital_cap: Decimal = Field(gt=0)
    allow_futures: bool = False


class DisarmIn(BaseModel):
    account_id: str | None = None  # None disarms every account
    flatten: bool = True


class ResetIn(BaseModel):
    mode: str = Field(default="PAPER", pattern="^(PAPER|LIVE)$")


def _autopilot(request: Request) -> Autopilot:
    return ctx(request).services["autopilot"]


@router.get("/autopilot")
def status(request: Request, principal: Principal = Depends(require("autopilot:view"))) -> dict[str, Any]:
    autopilot = _autopilot(request)
    runs = autopilot.runs(1)
    return {**autopilot.status(), "latest_run": encode(runs[0]) if runs else None}


@router.put("/autopilot/config")
def configure(
    body: dict[str, Any], request: Request, principal: Principal = Depends(require("autopilot:configure"))
) -> dict[str, Any]:
    return encode(_autopilot(request).update_config(body, principal.user_id))


@router.post("/autopilot:run", status_code=202)
def run(request: Request, principal: Principal = Depends(require("autopilot:run"))) -> dict[str, Any]:
    autopilot = _autopilot(request)
    started = autopilot.run_in_background()
    ctx(request).audit.record(actor=principal.user_id, action="autopilot.run", category="TRADING")
    return {"started": started, "progress": autopilot.progress}


@router.get("/autopilot/runs")
def runs(request: Request, principal: Principal = Depends(require("autopilot:view"))) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in encode(r).items() if k not in ("leaderboard", "selected")}
        | {"selected_count": len(r.selected)}
        for r in _autopilot(request).runs(30)
    ]


@router.get("/autopilot/runs/{run_id}")
def run_detail(
    run_id: str, request: Request, principal: Principal = Depends(require("autopilot:view"))
) -> dict[str, Any]:
    for r in _autopilot(request).runs(100):
        if r.run_id == run_id:
            return encode(r)
    raise NotFoundError("RUN_NOT_FOUND", f"unknown run {run_id}")


@router.get("/autopilot/decisions")
def decisions(
    request: Request, limit: int = 100, principal: Principal = Depends(require("autopilot:view"))
) -> list[dict[str, Any]]:
    return [encode(d) for d in _autopilot(request).decisions(min(limit, 500))]


@router.post("/autopilot/live:arm")
@locked
def arm(body: ArmIn, request: Request, principal: Principal = Depends(require("autopilot:arm"))):
    """Allow the autopilot to promote proven paper strategies to real orders, up to a capital cap."""
    return encode(
        _autopilot(request).arm_live(
            body.account_id, body.capital_cap, principal.user_id, allow_futures=body.allow_futures
        )
    )


@router.post("/autopilot/live:disarm")
def disarm(body: DisarmIn, request: Request, principal: Principal = Depends(require("autopilot:disarm"))):
    return encode(
        _autopilot(request).disarm_live(principal.user_id, account_id=body.account_id, flatten=body.flatten)
    )


@router.get("/autopilot/universe")
def universe(
    request: Request, principal: Principal = Depends(require("autopilot:view"))
) -> list[dict[str, Any]]:
    """Everything the universe may contain, grouped by asset class; futures as rolling front months."""
    return _autopilot(request).universe_options()


@router.get("/autopilot/monitor")
def monitor_status(
    request: Request, principal: Principal = Depends(require("autopilot:view"))
) -> dict[str, Any]:
    """The AI trade monitor: latest verdict per open position, entries waiting, and its track record."""
    c = ctx(request)
    monitor = c.services["monitor"]
    config = c.services["autopilot"].config
    return {
        **monitor.status(),
        "enabled": config.monitor_enabled,
        "mode": config.monitor_mode,
        "interval_seconds": config.monitor_interval_seconds,
        "min_confidence": config.monitor_min_confidence,
        "decision_mode": config.decision_mode,
        "ai_trading": c.services["autopilot"].ai_trading(),
    }


@router.post("/autopilot/monitor:run")
def monitor_run(request: Request, principal: Principal = Depends(require("autopilot:run"))):
    """Review the open positions now instead of waiting for the next minute."""
    reviews = ctx(request).services["monitor"].review_now()
    return {"reviews": [encode(r) for r in reviews]}


class ForgetIn(BaseModel):
    mistake: str = Field(pattern="^(weak_consensus|wide_spread|late_session|chased_move)$")


@router.get("/autopilot/lessons")
def lessons(request: Request, principal: Principal = Depends(require("autopilot:view"))) -> dict[str, Any]:
    """Post-mortems of losing trades and the rules learned from repeated mistakes."""
    return ctx(request).services["monitor"].lessons_status()


@router.post("/autopilot/lessons:forget")
def forget_rule(
    body: ForgetIn, request: Request, principal: Principal = Depends(require("autopilot:configure"))
) -> dict[str, Any]:
    """Stop enforcing a learned rule; only mistakes made after now count towards it again."""
    c = ctx(request)
    c.services["monitor"].lessons.forget(body.mistake)
    c.audit.record(
        actor=principal.user_id,
        action="autopilot.forget_rule",
        category="CONFIGURATION",
        data={"mistake": body.mistake},
    )
    return c.services["monitor"].lessons_status()


@router.post("/autopilot/protection:reset")
@locked
def reset_protection(
    body: ResetIn, request: Request, principal: Principal = Depends(require("autopilot:arm"))
):
    """Resume after a loss-floor stop. Privileged: it lets the autopilot risk money again."""
    autopilot = _autopilot(request)
    autopilot.reset_protection(body.mode, principal.user_id)
    return encode(autopilot.protection(body.mode))
