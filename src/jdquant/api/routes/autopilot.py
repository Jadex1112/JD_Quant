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


class DisarmIn(BaseModel):
    flatten: bool = True


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
    return encode(_autopilot(request).arm_live(body.account_id, body.capital_cap, principal.user_id))


@router.post("/autopilot/live:disarm")
def disarm(body: DisarmIn, request: Request, principal: Principal = Depends(require("autopilot:disarm"))):
    return encode(_autopilot(request).disarm_live(principal.user_id, flatten=body.flatten))
