"""The strategy lab: typed strategies translated by the AI, backtested with a confidence score."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from jdquant.api.convert import deployment_out
from jdquant.api.deps import ctx, locked, require
from jdquant.core.errors import PlatformError
from jdquant.persistence.codec import encode
from jdquant.platform import PAPER_ACCOUNT_ID
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["strategy-lab"])


class TranslateIn(BaseModel):
    text: str = Field(min_length=5, max_length=4000)
    instrument_id: str
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    model: str | None = Field(default=None, max_length=120)


class CheckIn(BaseModel):
    spec: dict[str, Any]


class LabBacktestIn(BaseModel):
    spec: dict[str, Any]
    instrument_id: str
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    bars: int = Field(default=2000, ge=200, le=20000)
    capital: Decimal = Field(default=Decimal(100_000), gt=0)
    leverage: Decimal = Field(default=Decimal(1), ge=1, le=50)
    text: str = Field(default="", max_length=4000)
    assumptions: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


class PaperIn(BaseModel):
    account_id: str = PAPER_ACCOUNT_ID


def _lab(request: Request):
    return ctx(request).services["lab"]


@router.post("/strategy-lab/translate")
def translate(body: TranslateIn, request: Request, principal: Principal = Depends(require("backtest:run"))):
    """Plain words -> validated rules, with the assumptions made and anything that could not be expressed."""
    return _lab(request).translate(body.text, body.instrument_id, body.interval_seconds, body.model)


@router.get("/strategy-lab/models")
def models(request: Request, principal: Principal = Depends(require("backtest:run"))) -> dict[str, Any]:
    """The AI that writes rules, preset NVIDIA models (Nemotron, Kimi) and what the key can reach."""
    return _lab(request).models()


@router.post("/strategy-lab/code")
def code(body: CheckIn, principal: Principal = Depends(require("backtest:run"))) -> dict[str, str]:
    """The rules as readable Python, generated without AI; the platform runs the rules themselves."""
    from jdquant.strategy.rules import rules_to_python

    return {"code": rules_to_python(body.spec)}


@router.get("/strategy-lab/vocabulary")
def vocabulary(principal: Principal = Depends(require("backtest:run"))) -> dict[str, Any]:
    """What a rule can be built from, for the visual builder (the same lists the validator uses)."""
    from jdquant.strategy.rules import CHOICES, INDICATORS, OPS, SIDES

    return {
        "indicators": {
            name: {
                "params": {p: {"default": d, "min": lo, "max": hi} for p, (d, lo, hi) in params.items()},
                "choice": {"key": CHOICES[name][0], "options": list(CHOICES[name][1])}
                if name in CHOICES
                else None,
            }
            for name, params in INDICATORS.items()
        },
        "ops": list(OPS),
        "sides": list(SIDES),
    }


@router.post("/strategy-lab/check")
def check(body: CheckIn, principal: Principal = Depends(require("backtest:run"))) -> dict[str, Any]:
    """Validate rules built by hand and describe them in plain English (no AI involved)."""
    from jdquant.strategy.rules import describe, rules_to_python, validate_spec

    spec = validate_spec(body.spec)
    return {"spec": spec, "description": describe(spec), "code": rules_to_python(spec)}


@router.post("/strategy-lab/backtests", status_code=201)
def backtest(body: LabBacktestIn, request: Request, principal: Principal = Depends(require("backtest:run"))):
    run = _lab(request).backtest(
        body.spec,
        body.instrument_id,
        interval_seconds=body.interval_seconds,
        bars=body.bars,
        capital=body.capital,
        leverage=body.leverage,
        user_id=principal.user_id,
        text=body.text,
        assumptions=body.assumptions,
        unsupported=body.unsupported,
    )
    return encode(run)


@router.get("/strategy-lab/runs")
def runs(request: Request, principal: Principal = Depends(require("backtest:run"))) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in encode(r).items() if k not in ("equity", "trades")}
        for r in _lab(request).runs(principal.user_id)
    ]


@router.get("/strategy-lab/runs/{run_id}")
def run_detail(run_id: str, request: Request, principal: Principal = Depends(require("backtest:run"))):
    run = _lab(request).get(run_id)
    if run.user_id != principal.user_id and not principal.can("user:view"):
        raise PlatformError("LAB_RUN_NOT_FOUND", "unknown lab run")
    return encode(run)


@router.post("/strategy-lab/runs/{run_id}:paper-trade", status_code=201)
@locked
def paper_trade(
    run_id: str, body: PaperIn, request: Request, principal: Principal = Depends(require("deployment:create"))
):
    """Deploy the tested rules on a paper account; they then trade live prices."""
    if not principal.can("deployment:start"):
        raise PlatformError("PERMISSION_DENIED", "starting deployments is not permitted for this user")
    deployment = _lab(request).paper_trade(run_id, body.account_id, principal.user_id)
    ctx(request).audit.record(
        actor=principal.user_id, action="lab.paper_trade", category="TRADING", data={"run_id": run_id}
    )
    return deployment_out(deployment)
