"""The AI research desk: analysts, a bull/bear debate, a trader, a risk team and a portfolio manager."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, require
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1/research-desk", tags=["research desk"])


def _desk(request: Request):
    return ctx(request).services["desk"]


class RunIn(BaseModel):
    instrument_id: str
    interval_seconds: int = Field(default=86400, ge=300, le=604800)
    horizon: int = Field(default=5, ge=1, le=60)
    debate_rounds: int = Field(default=1, ge=1, le=3)
    model: str | None = Field(default=None, max_length=120)
    use_forecast: bool = True


@router.post("/reports", status_code=202)
def run(body: RunIn, request: Request, principal: Principal = Depends(require("ai.copilot:use"))):
    from jdquant.ai.analyst import MODEL_ID
    from jdquant.core.errors import ValidationError

    if body.model and not MODEL_ID.match(body.model):
        raise ValidationError(
            "MODEL_INVALID", [{"field": "model", "message": "a model id like moonshotai/kimi-k3"}]
        )
    return _desk(request).start(
        body.instrument_id,
        user=principal.user_id,
        interval_seconds=body.interval_seconds,
        horizon=body.horizon,
        debate_rounds=body.debate_rounds,
        model=body.model,
        use_forecast=body.use_forecast,
    )


@router.get("/reports")
def reports(
    request: Request,
    instrument_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    desk = _desk(request)
    items = [
        {k: v for k, v in r.items() if k not in ("steps", "data")} for r in desk.list(instrument_id, limit)
    ]
    return {"reports": items, "scorecard": desk.scorecard()}


@router.get("/reports/{report_id}")
def report(report_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    return _desk(request).get(report_id)
