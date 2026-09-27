"""AI endpoints: features, training, models, inference, optimizer and copilot (Chapters 52–59, 63)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from jdquant.ai.copilot import Conversation
from jdquant.ai.features import DEFAULT_FEATURES, LIBRARY, build_feature_set
from jdquant.ai.optimizer import optimize
from jdquant.ai.training import TrainingConfig, train
from jdquant.api.deps import ctx, locked, require
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.records import Candle
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.persistence.codec import encode
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["ai"])


class DataSpec(BaseModel):
    source: str = Field(default="synthetic", description="synthetic or venue")
    bars: int = Field(default=2000, ge=100, le=20000)
    interval_seconds: int = 3600
    seed: int = 7
    start_price: str = "100"
    volatility: float = Field(default=0.01, gt=0, le=0.5)
    drift: float = 0.0


class TrainIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    instrument_id: str
    features: list[dict[str, Any]] = Field(default_factory=lambda: list(DEFAULT_FEATURES))
    algorithm: str = "logistic_regression"
    horizon: int = Field(default=1, ge=1, le=100)
    test_fraction: float = Field(default=0.25, gt=0.05, lt=0.9)
    l2: float = Field(default=1.0, ge=0)
    cost_bps: float = Field(default=5.0, ge=0)
    data: DataSpec = Field(default_factory=DataSpec)


class PromoteIn(BaseModel):
    stage: str
    reason: str = ""


class PredictIn(BaseModel):
    instrument_id: str
    data: DataSpec = Field(default_factory=lambda: DataSpec(bars=300))


class OptimizeIn(BaseModel):
    instruments: list[str] = Field(min_length=2)
    method: str = "MIN_VARIANCE"
    long_only: bool = True
    max_weight: float = Field(default=1.0, gt=0, le=1)
    data: DataSpec = Field(default_factory=lambda: DataSpec(bars=500, interval_seconds=86400))


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    context: str = ""


def load_candles(request: Request, instrument_id: str, spec: DataSpec, seed_offset: int = 0) -> list[Candle]:
    c = ctx(request)
    instrument = c.platform.instruments.get(instrument_id)
    if spec.source == "venue":
        source = c.services["connections"].data_source_for(instrument_id)
        if source is None:
            raise PlatformError("DATA_UNAVAILABLE", f"no connected data source for {instrument_id}")
        return source.fetch_candles(instrument, spec.interval_seconds, min(spec.bars, 1000))
    if spec.source != "synthetic":
        raise ValidationError(
            "DATA_SOURCE_INVALID", [{"field": "data.source", "message": "synthetic or venue"}]
        )
    return random_walk_candles(
        instrument,
        datetime(2024, 1, 1, tzinfo=UTC),
        spec.bars,
        interval_seconds=spec.interval_seconds,
        start_price=Decimal(spec.start_price),
        volatility=spec.volatility,
        drift=spec.drift,
        seed=spec.seed + seed_offset,
    )


def _version_out(v) -> dict[str, Any]:
    return {
        "model": v.model,
        "version": v.version,
        "stage": v.stage.value,
        "algorithm": v.artifact.algorithm,
        "features": v.feature_set.names,
        "instrument_id": v.instrument_id,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat(),
        "checksum": v.checksum,
        "rollback_target": v.rollback_target,
        "approvals": v.approvals,
        "model_card": v.model_card,
        "report": v.report,
    }


# ---- features & models ------------------------------------------------------------------------


@router.get("/features/library")
def feature_library(principal: Principal = Depends(require("feature:view"))) -> list[dict]:
    return [{"kind": k, "description": d} for k, (_, d) in LIBRARY.items()]


@router.post("/models:train", status_code=201)
def train_model(body: TrainIn, request: Request, principal: Principal = Depends(require("model:train"))):
    feature_set = build_feature_set(f"{body.name}-features", body.features)
    candles = load_candles(request, body.instrument_id, body.data)
    config = TrainingConfig(
        body.algorithm,
        body.horizon,
        body.test_fraction,
        l2=body.l2,
        cost_bps=body.cost_bps,
        seed=body.data.seed,
    )
    model, report = train(feature_set, candles, config)
    report["data"] = body.data.model_dump()
    registry = ctx(request).services["models"]
    version = registry.register(
        body.name, model, feature_set, report, created_by=principal.user_id, instrument_id=body.instrument_id
    )
    ctx(request).audit.record(
        actor=principal.user_id, action="model.train", category="AI", target=f"{body.name}:v{version.version}"
    )
    return _version_out(version)


@router.get("/models")
def list_models(request: Request, principal: Principal = Depends(require("model:view"))) -> list[dict]:
    registry = ctx(request).services["models"]
    return [
        {"model": name, "versions": [_version_out(v) for v in registry.versions(name)]}
        for name in registry.models()
    ]


@router.post("/models/{name}/versions/{version}:promote")
def promote(
    name: str,
    version: int,
    body: PromoteIn,
    request: Request,
    principal: Principal = Depends(require("model:promote")),
):
    from jdquant.ai.models import Stage

    try:
        stage = Stage(body.stage)
    except ValueError:
        raise ValidationError(
            "STAGE_INVALID", [{"field": "stage", "message": "STAGING, SHADOW or PRODUCTION"}]
        ) from None
    mv = (
        ctx(request)
        .services["models"]
        .promote(name, version, stage, approver=principal.user_id, reason=body.reason)
    )
    ctx(request).audit.record(
        actor=principal.user_id,
        action="model.promote",
        category="AI",
        target=f"{name}:v{version}",
        reason=body.reason,
        data={"stage": stage.value},
    )
    return _version_out(mv)


@router.post("/models/{name}:rollback")
def rollback(name: str, request: Request, principal: Principal = Depends(require("model:rollback"))):
    mv = ctx(request).services["models"].rollback(name, actor=principal.user_id)
    ctx(request).audit.record(actor=principal.user_id, action="model.rollback", category="AI", target=name)
    return _version_out(mv)


@router.post("/models/{name}:predict")
def predict(
    name: str, body: PredictIn, request: Request, principal: Principal = Depends(require("model:view"))
):
    candles = load_candles(request, body.instrument_id, body.data)
    prediction = ctx(request).services["models"].predict(name, candles, instrument_id=body.instrument_id)
    return encode(prediction)


@router.get("/models/{name}/drift")
def drift(name: str, request: Request, principal: Principal = Depends(require("model:view"))):
    return ctx(request).services["models"].drift(name)


# ---- portfolio optimizer ----------------------------------------------------------------------


@router.post("/portfolio:optimize")
def optimize_portfolio(
    body: OptimizeIn, request: Request, principal: Principal = Depends(require("portfolio:view"))
):
    returns = {}
    for i, instrument_id in enumerate(body.instruments):
        closes = [float(c.close) for c in load_candles(request, instrument_id, body.data, seed_offset=i)]
        returns[instrument_id] = [closes[j] / closes[j - 1] - 1 for j in range(1, len(closes))]
    length = min(len(r) for r in returns.values())
    returns = {k: v[-length:] for k, v in returns.items()}
    periods = 365 * 86400 / body.data.interval_seconds
    return encode(
        optimize(
            returns,
            body.method,
            long_only=body.long_only,
            max_weight=body.max_weight,
            periods_per_year=periods,
        )
    )


# ---- copilot ----------------------------------------------------------------------------------


def _conversation_out(conv: Conversation) -> dict[str, Any]:
    return {
        "conversation_id": conv.conversation_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat(),
        "transcript": encode(conv.transcript),
        "pending_actions": [encode(a) for a in conv.pending if a.status.value == "PENDING"],
    }


@router.get("/copilot/conversations")
def conversations(request: Request, principal: Principal = Depends(require("ai.copilot:use"))):
    return [
        {"conversation_id": c.conversation_id, "title": c.title, "created_at": c.created_at.isoformat()}
        for c in ctx(request).services["copilot"].list(principal)
    ]


@router.post("/copilot/conversations", status_code=201)
def new_conversation(request: Request, principal: Principal = Depends(require("ai.copilot:use"))):
    return _conversation_out(ctx(request).services["copilot"].start(principal))


@router.get("/copilot/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, request: Request, principal: Principal = Depends(require("ai.copilot:use"))
):
    return _conversation_out(ctx(request).services["copilot"].get(principal, conversation_id))


@router.post("/copilot/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    body: MessageIn,
    request: Request,
    principal: Principal = Depends(require("ai.copilot:use")),
):
    conv = ctx(request).services["copilot"].send(principal, conversation_id, body.text, context=body.context)
    return _conversation_out(conv)


def _resolve(approve: bool):
    @locked
    def handler(
        conversation_id: str,
        action_id: str,
        request: Request,
        principal: Principal = Depends(require("ai.copilot:use")),
    ):
        conv = ctx(request).services["copilot"].resolve(principal, conversation_id, action_id, approve)
        return _conversation_out(conv)

    return handler


router.add_api_route(
    "/copilot/conversations/{conversation_id}/actions/{action_id}:confirm", _resolve(True), methods=["POST"]
)
router.add_api_route(
    "/copilot/conversations/{conversation_id}/actions/{action_id}:decline", _resolve(False), methods=["POST"]
)
