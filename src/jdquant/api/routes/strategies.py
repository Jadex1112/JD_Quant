"""Strategy versions and their path to live money: DEVELOPMENT -> BACKTEST -> VALIDATION -> PAPER ->
APPROVED -> LIVE, with rollback. Each step's gate is enforced by the registry, not by this API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, require
from jdquant.platform import PAPER_ACCOUNT_ID
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1/strategies", tags=["strategy-registry"])


def _reg(request: Request):
    return ctx(request).services["registry"]


class CreateIn(BaseModel):
    name: str = Field(default="", max_length=80)
    template: str
    instrument_id: str
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=2000)


class FromLabIn(BaseModel):
    run_id: str
    name: str = Field(default="", max_length=80)


class VersionIn(BaseModel):
    parameters: dict[str, Any]
    notes: str = Field(default="", max_length=2000)


class BacktestIn(BaseModel):
    bars: int = Field(default=2000, ge=200, le=20000)
    synthetic_ok: bool = True


class ValidateIn(BaseModel):
    bars: int = Field(default=3000, ge=400, le=20000)


class AccountIn(BaseModel):
    account_id: str = PAPER_ACCOUNT_ID


class ApproveIn(BaseModel):
    note: str = Field(default="", max_length=500)


class SettingsIn(BaseModel):
    validation_folds: int | None = Field(default=None, ge=2, le=12)
    min_validation_trades: int | None = Field(default=None, ge=0, le=10_000)
    min_paper_days: float | None = Field(default=None, ge=0, le=365)
    min_paper_trades: int | None = Field(default=None, ge=0, le=10_000)


@router.get("")
def list_strategies(request: Request, principal: Principal = Depends(require("deployment:view"))):
    reg = _reg(request)
    out = []
    for doc in reg.list():
        latest = doc["versions"][-1] if doc["versions"] else {}
        out.append(
            {
                "strategy_id": doc["strategy_id"],
                "name": doc["name"],
                "template": doc["template"],
                "instrument_id": doc["instrument_id"],
                "interval_seconds": doc["interval_seconds"],
                "live_version": doc.get("live_version"),
                "latest_version": latest.get("version"),
                "latest_stage": latest.get("stage"),
                "versions": len(doc["versions"]),
            }
        )
    return {"strategies": out, "settings": reg.settings.to_dict()}


@router.get("/settings")
def get_settings(request: Request, principal: Principal = Depends(require("deployment:view"))):
    return _reg(request).settings.to_dict()


@router.put("/settings")
def put_settings(
    body: SettingsIn, request: Request, principal: Principal = Depends(require("deployment:approve"))
):
    return _reg(request).configure(body.model_dump(exclude_none=True))


@router.post("", status_code=201)
def create(body: CreateIn, request: Request, principal: Principal = Depends(require("deployment:create"))):
    reg = _reg(request)
    d = reg.create(
        name=body.name,
        template=body.template,
        instrument_id=body.instrument_id,
        interval_seconds=body.interval_seconds,
        parameters=body.parameters,
        description=body.description,
        user=principal.user_id,
    )
    return reg.view(d.strategy_id)


@router.post("/from-lab", status_code=201)
def from_lab(body: FromLabIn, request: Request, principal: Principal = Depends(require("deployment:create"))):
    reg = _reg(request)
    d = reg.from_lab(ctx(request).services["lab"], body.run_id, name=body.name, user=principal.user_id)
    return reg.view(d.strategy_id)


@router.get("/{strategy_id}")
def get_strategy(
    strategy_id: str, request: Request, principal: Principal = Depends(require("deployment:view"))
):
    return _reg(request).view(strategy_id)


@router.post("/{strategy_id}/versions", status_code=201)
def new_version(
    strategy_id: str,
    body: VersionIn,
    request: Request,
    principal: Principal = Depends(require("deployment:create")),
):
    reg = _reg(request)
    reg.new_version(strategy_id, body.parameters, body.notes, principal.user_id)
    return reg.view(strategy_id)


@router.post("/{strategy_id}/versions/{version}/backtest")
def backtest(
    strategy_id: str,
    version: str,
    body: BacktestIn,
    request: Request,
    principal: Principal = Depends(require("backtest:run")),
):
    return _reg(request).backtest(
        strategy_id, version, principal.user_id, bars=body.bars, synthetic_ok=body.synthetic_ok
    )


@router.post("/{strategy_id}/versions/{version}/validate")
def validate(
    strategy_id: str,
    version: str,
    body: ValidateIn,
    request: Request,
    principal: Principal = Depends(require("backtest:run")),
):
    return _reg(request).validate(strategy_id, version, principal.user_id, bars=body.bars)


@router.post("/{strategy_id}/versions/{version}/paper")
def paper(
    strategy_id: str,
    version: str,
    body: AccountIn,
    request: Request,
    principal: Principal = Depends(require("deployment:start")),
):
    return _reg(request).start_paper(strategy_id, version, body.account_id, principal.user_id)


@router.post("/{strategy_id}/versions/{version}/approve")
def approve(
    strategy_id: str,
    version: str,
    body: ApproveIn,
    request: Request,
    principal: Principal = Depends(require("deployment:approve")),
):
    reg = _reg(request)
    reg.approve(strategy_id, version, principal.user_id, body.note)
    return reg.view(strategy_id)


@router.post("/{strategy_id}/versions/{version}/live")
def go_live(
    strategy_id: str,
    version: str,
    body: AccountIn,
    request: Request,
    principal: Principal = Depends(require("deployment:approve")),
):
    return _reg(request).go_live(strategy_id, version, body.account_id, principal.user_id)


@router.post("/{strategy_id}/rollback")
def rollback(
    strategy_id: str, request: Request, principal: Principal = Depends(require("deployment:approve"))
):
    return _reg(request).rollback(strategy_id, principal.user_id)


@router.post("/{strategy_id}/versions/{version}/retire")
def retire(
    strategy_id: str,
    version: str,
    request: Request,
    principal: Principal = Depends(require("deployment:retire")),
):
    reg = _reg(request)
    reg.retire(strategy_id, version, principal.user_id)
    return reg.view(strategy_id)
