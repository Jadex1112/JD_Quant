"""Kronos forecasts and their forward scorecard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, require
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"])


def _svc(request: Request):
    return ctx(request).services["forecasts"]


class ForecastIn(BaseModel):
    instrument_id: str
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    horizon: int = Field(default=12, ge=1, le=120)
    lookback: int = Field(default=400, ge=60, le=2048)


class SettingsIn(BaseModel):
    model: str | None = None
    paths: int | None = None


@router.get("/status")
def status(request: Request, principal: Principal = Depends(require("marketdata:view"))) -> dict[str, Any]:
    return _svc(request).status()


@router.put("/settings")
def settings(
    body: SettingsIn, request: Request, principal: Principal = Depends(require("marketdata:subscribe"))
) -> dict[str, Any]:
    return _svc(request).configure(model=body.model, paths=body.paths)


@router.post("")
def forecast(body: ForecastIn, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    return _svc(request).forecast(
        body.instrument_id,
        interval_seconds=body.interval_seconds,
        horizon=body.horizon,
        lookback=body.lookback,
    )


@router.get("")
def history(
    request: Request,
    instrument_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(require("marketdata:view")),
):
    svc = _svc(request)
    return {"forecasts": svc.list(instrument_id, limit), "scorecard": svc.scorecard(instrument_id)}
