import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from conftest import BTC, T0, market, set_quote

from jdquant.ai.copilot import Copilot
from jdquant.ai.copilot_tools import build_tools
from jdquant.ai.features import DEFAULT_FEATURES, build_feature_set
from jdquant.ai.models import ModelRegistry, Stage
from jdquant.ai.optimizer import optimize
from jdquant.ai.prompts import redact
from jdquant.ai.training import TrainingConfig, purged_split, train
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.marketdata.records import Candle
from jdquant.persistence.store import Store
from jdquant.platform import demo_instruments
from jdquant.security.audit import AuditLog
from jdquant.security.identity import AuthMethod, Principal
from jdquant.security.permissions import permissions_for

INSTRUMENT = demo_instruments()[0]


def momentum_candles(n=1500, phi=0.4, seed=3) -> list[Candle]:
    """AR(1) returns: yesterday's return predicts today's, so a model has something to learn."""
    rng = Random(seed)
    price, prev, out = 100.0, 0.0, []
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(n):
        r = phi * prev + rng.gauss(0, 0.01)
        new = price * math.exp(r)
        o, c = Decimal(f"{price:.2f}"), Decimal(f"{new:.2f}")
        out.append(
            Candle(
                INSTRUMENT.instrument_id,
                3600,
                start + timedelta(hours=i),
                start + timedelta(hours=i + 1),
                o,
                max(o, c),
                min(o, c),
                c,
                Decimal(100 + i % 7),
            )
        )
        price, prev = new, r
    return out


FS = build_feature_set("fs", DEFAULT_FEATURES)


# ---- feature store & training ------------------------------------------------------------------


def test_online_and_offline_features_match():
    """AI-63004, AI-63005."""
    candles = momentum_candles(300)
    offline = dict(FS.materialize(candles))
    for i in range(50, 300, 37):
        online = FS.vector(candles[: i + 1])
        assert online == offline[candles[i].close_ts]


def test_purged_split_leaves_a_gap():
    """AI-58003."""
    train_idx, test_idx = purged_split(1000, 0.25, horizon=5, embargo=10)
    assert test_idx[0] - train_idx[-1] - 1 == 15


def test_training_learns_momentum_and_is_reproducible():
    candles = momentum_candles()
    model, report = train(FS, candles, TrainingConfig(horizon=1))
    test = report["metrics"]["test"]
    assert test["auc"] > 0.6
    assert test["accuracy"] > test["baseline_accuracy"]
    assert report["samples"]["purged"] > 0
    again, _ = train(FS, candles, TrainingConfig(horizon=1))
    assert again.checksum == model.checksum
    assert json.loads(json.dumps(model.to_dict()))["weights"] == model.weights


def test_noise_produces_warnings():
    from jdquant.marketdata.synthetic import random_walk_candles

    candles = random_walk_candles(INSTRUMENT, T0, 800, seed=5)
    _, report = train(FS, candles, TrainingConfig(horizon=1, cost_bps=20))
    assert report["warnings"]


# ---- model registry & inference ----------------------------------------------------------------


@pytest.fixture
def registry():
    return ModelRegistry(Store(":memory:"), SimulatedClock(T0), single_user=False)


def _register(registry, name="mom", creator="alice"):
    model, report = train(FS, momentum_candles(), TrainingConfig())
    return registry.register(name, model, FS, report, created_by=creator, instrument_id=BTC)


def test_promotion_gates_and_rollback(registry):
    """AI-57003, AI-57004, AI-57006."""
    v1 = _register(registry)
    with pytest.raises(PlatformError, match="INVALID_STATE_TRANSITION"):
        registry.promote("mom", 1, Stage.PRODUCTION, approver="bob")
    registry.promote("mom", 1, Stage.STAGING, approver="alice")
    registry.promote("mom", 1, Stage.SHADOW, approver="alice")
    with pytest.raises(PlatformError) as err:
        registry.promote("mom", 1, Stage.PRODUCTION, approver="alice")
    assert err.value.code == "PROMOTION_BLOCKED"
    registry.promote("mom", 1, Stage.PRODUCTION, approver="bob")

    _register(registry)
    for stage in (Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION):
        registry.promote("mom", 2, stage, approver="bob")
    assert registry.in_stage("mom", Stage.PRODUCTION).version == 2
    assert registry.get("mom", 2).rollback_target == v1.version
    registry.rollback("mom", actor="bob")
    assert registry.in_stage("mom", Stage.PRODUCTION).version == 1
    assert registry.get("mom", 2).stage is Stage.ARCHIVED


def test_weak_models_cannot_reach_production(registry):
    from jdquant.marketdata.synthetic import random_walk_candles

    model, report = train(FS, random_walk_candles(INSTRUMENT, T0, 800, seed=5), TrainingConfig())
    report["metrics"]["test"]["auc"] = 0.45
    registry.register("noise", model, FS, report, created_by="alice")
    registry.promote("noise", 1, Stage.STAGING, approver="bob")
    registry.promote("noise", 1, Stage.SHADOW, approver="bob")
    with pytest.raises(PlatformError, match="AUC"):
        registry.promote("noise", 1, Stage.PRODUCTION, approver="bob")


def test_tampered_artifacts_are_rejected(registry):
    _register(registry)
    doc = registry._store.get("model_version:mom", "1")
    doc["artifact"]["weights"][1] += 1.0
    registry._store.put("model_version:mom", "1", doc)
    with pytest.raises(PlatformError, match="MODEL_CHECKSUM_MISMATCH"):
        registry.get("mom", 1)


def test_predictions_are_recorded_with_shadow_scores(registry):
    """CON-145, AI-57005, AI-59003."""
    _register(registry)
    for stage in (Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION):
        registry.promote("mom", 1, stage, approver="bob")
    _register(registry)
    registry.promote("mom", 2, Stage.STAGING, approver="bob")
    registry.promote("mom", 2, Stage.SHADOW, approver="bob")
    candles = momentum_candles(200)
    prediction = registry.predict("mom", candles, instrument_id=BTC)
    assert prediction.version == 1 and 0 <= prediction.value <= 1
    assert "v2" in prediction.shadow
    assert registry.inference_count("mom") == 1
    with pytest.raises(PlatformError, match="INPUT_INVALID"):
        registry.predict("mom", candles[:5])


def test_drift_alert_on_shifted_inputs(registry):
    """AI-57008, AI-57009."""
    _register(registry)
    for stage in (Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION):
        registry.promote("mom", 1, stage, approver="bob")
    events = []
    from jdquant.core.events import EventBus

    registry._bus = EventBus(registry._clock)
    registry._bus.subscribe("model.drift.*", events.append)
    calm = momentum_candles(400, phi=0.0, seed=9)
    wild = [
        Candle(
            c.instrument_id,
            c.interval_seconds,
            c.open_ts,
            c.close_ts,
            c.open,
            c.high * 2,
            c.low / 2,
            c.close * (2 if i % 2 else 1),
            c.volume,
        )
        for i, c in enumerate(calm)
    ]
    for i in range(100, 160):
        registry.predict("mom", wild[: i + 1], record=False)
    drift = registry.drift("mom")
    assert max(v for v in drift.values() if v is not None) > 0.25
    assert events


def test_ml_signal_strategy_backtests_with_a_production_model(registry):
    _register(registry, creator="alice")
    for stage in (Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION):
        registry.promote("mom", 1, stage, approver="bob")
    candles = momentum_candles(600, seed=11)
    result = run_backtest(
        BacktestConfig(
            "ml_signal",
            [INSTRUMENT],
            {INSTRUMENT.instrument_id: candles},
            {"model": "mom", "threshold": "0.02", "quantity": "1"},
            models=registry,
            slippage_bps=Decimal(0),
        )
    )
    assert result.strategy_errors == 0 and result.trades
    assert registry.inference_count("mom") == 0  # backtests do not write inference records


# ---- optimizer ---------------------------------------------------------------------------------


def _returns(n=400, seed=1):
    rng = np.random.default_rng(seed)
    vols = [0.01, 0.02, 0.015]
    return {f"A{i}": rng.normal(0.0005, v, n).tolist() for i, v in enumerate(vols)}


def test_min_variance_matches_the_analytical_solution():
    """AC-55001."""
    rets = _returns()
    result = optimize(rets, "MIN_VARIANCE", periods_per_year=252)
    cov = np.cov(np.array([rets[k] for k in sorted(rets)]))
    analytic = np.linalg.solve(cov, np.ones(3))
    analytic /= analytic.sum()
    assert np.allclose(list(result.weights.values()), analytic, atol=1e-6)


def test_risk_parity_equalizes_contributions_and_bounds_hold():
    result = optimize(_returns(), "RISK_PARITY")
    contributions = list(result.risk_contributions.values())
    assert max(contributions) - min(contributions) < 1e-6
    capped = optimize(_returns(), "MAX_SHARPE", max_weight=0.4)
    assert all(-1e-9 <= w <= 0.4 + 1e-9 for w in capped.weights.values())
    assert abs(sum(capped.weights.values()) - 1) < 1e-9


def test_infeasible_constraints_are_reported():
    """AC-55002."""
    rets = {f"A{i}": _returns()["A0"] for i in range(4)}
    with pytest.raises(PlatformError, match="OPTIMIZATION_INFEASIBLE"):
        optimize(rets, "MIN_VARIANCE", max_weight=0.2)


# ---- copilot -----------------------------------------------------------------------------------


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


def reply(*blocks, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=list(blocks),
        model="claude-opus-5",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


class FakeClaude:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.responses.pop(0)


def _principal(roles):
    return Principal(
        "u1", "u1@example.com", "Una", tuple(roles), permissions_for(list(roles)), AuthMethod.SESSION
    )


def _copilot(platform, fake, **kw):
    from jdquant.api.context import Settings, build_context

    context = build_context(Settings(), platform)
    context.services["models"] = ModelRegistry(context.store, platform.clock)
    return Copilot(
        context.store,
        platform.clock,
        AuditLog(context.store, platform.clock),
        build_tools(context),
        client_factory=lambda: fake,
        **kw,
    ), context


def _running_deployment(platform):
    dep = platform.trading.create_deployment(
        strategy_name="ma_crossover",
        strategy_version="1.0.0",
        account_id="paper-main",
        parameters={},
        instruments=[BTC],
        created_by="u1",
    )
    platform.trading.approve(dep.deployment_id, "u1")
    platform.trading.start(dep.deployment_id)
    return dep


def test_copilot_answers_from_read_only_tools(platform):
    platform.oms.submit(market(Side.BUY, "0.1"))
    fake = FakeClaude(
        reply(Block("tool_use", id="t1", name="get_positions", input={}), stop="tool_use"),
        reply(Block("text", text="You hold 0.1 BTC on paper-main.")),
    )
    copilot, _ = _copilot(platform, fake)
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    conv = copilot.send(principal, conv.conversation_id, "What do I hold? my api_secret=hunter2hunter2")
    assert conv.transcript[-1].text == "You hold 0.1 BTC on paper-main."
    first, second = fake.requests
    assert first["model"] == "claude-opus-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"] and first["thinking"] == {"type": "adaptive"}
    assert "hunter2" not in json.dumps(first["messages"])
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and "BINANCE:BTCUSDT" in tool_result["content"]


def test_state_changing_tools_wait_for_confirmation(platform):
    """AI-52004, CON-140."""
    dep = _running_deployment(platform)
    fake = FakeClaude(
        reply(
            Block("tool_use", id="t1", name="pause_deployment", input={"deployment_id": dep.deployment_id}),
            stop="tool_use",
        ),
        reply(Block("text", text="Paused.")),
    )
    copilot, context = _copilot(platform, fake)
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    conv = copilot.send(principal, conv.conversation_id, "pause my BTC strategy")
    assert dep.state.value == "RUNNING" and len(fake.requests) == 1
    action = conv.pending[0]
    assert action.summary == f"Pause deployment {dep.deployment_id}"
    with pytest.raises(PlatformError, match="COPILOT_ACTION_PENDING"):
        copilot.send(principal, conv.conversation_id, "hello?")
    conv = copilot.resolve(principal, conv.conversation_id, action.action_id, approve=True)
    assert dep.state.value == "PAUSED"
    assert conv.transcript[-1].text == "Paused."
    assert context.audit.search(action="copilot.pause_deployment")


def test_declined_actions_do_nothing(platform):
    dep = _running_deployment(platform)
    fake = FakeClaude(
        reply(
            Block("tool_use", id="t1", name="stop_deployment", input={"deployment_id": dep.deployment_id}),
            stop="tool_use",
        ),
        reply(Block("text", text="Understood, left it running.")),
    )
    copilot, _ = _copilot(platform, fake)
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    conv = copilot.send(principal, conv.conversation_id, "stop it")
    conv = copilot.resolve(principal, conv.conversation_id, conv.pending[0].action_id, approve=False)
    assert dep.state.value == "RUNNING"
    assert "declined" in fake.requests[-1]["messages"][-1]["content"][0]["content"]


def test_copilot_respects_user_permissions(platform):
    """AI-52002: a viewer cannot act through the copilot."""
    dep = _running_deployment(platform)
    fake = FakeClaude(
        reply(
            Block("tool_use", id="t1", name="pause_deployment", input={"deployment_id": dep.deployment_id}),
            stop="tool_use",
        ),
        reply(Block("text", text="You don't have permission.")),
    )
    copilot, _ = _copilot(platform, fake)
    principal = _principal(["VIEWER"])
    conv = copilot.start(principal)
    conv = copilot.send(principal, conv.conversation_id, "pause it")
    assert not conv.pending and dep.state.value == "RUNNING"
    result = fake.requests[-1]["messages"][-1]["content"][0]
    assert result["is_error"] and "permission denied" in result["content"]


def test_conversations_are_private(platform):
    fake = FakeClaude()
    copilot, _ = _copilot(platform, fake)
    conv = copilot.start(_principal(["QUANT_TRADER"]))
    other = Principal(
        "u2", "u2@x.com", "Other", ("QUANT_TRADER",), permissions_for(["QUANT_TRADER"]), AuthMethod.SESSION
    )
    with pytest.raises(PlatformError, match="CONVERSATION_NOT_FOUND"):
        copilot.get(other, conv.conversation_id)


def test_copilot_unavailable_without_credentials(platform):
    def missing():
        raise RuntimeError("no API key")

    copilot, _ = _copilot(platform, None)
    copilot._client_factory = missing
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    with pytest.raises(PlatformError, match="COPILOT_UNAVAILABLE"):
        copilot.send(principal, conv.conversation_id, "hi")


def test_default_client_reports_missing_api_key(platform, monkeypatch):
    from jdquant.ai.copilot import _default_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    copilot, _ = _copilot(platform, None)
    copilot._client_factory = _default_client
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    with pytest.raises(PlatformError, match="set ANTHROPIC_API_KEY"):
        copilot.send(principal, conv.conversation_id, "hi")


def test_daily_quota_is_enforced(platform):
    fake = FakeClaude(reply(Block("text", text="ok")), reply(Block("text", text="ok")))
    copilot, _ = _copilot(platform, fake, daily_token_budget=100)
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    copilot.send(principal, conv.conversation_id, "one")
    with pytest.raises(PlatformError, match="COPILOT_QUOTA_EXCEEDED"):
        copilot.send(principal, conv.conversation_id, "two")


def test_diagnose_tool_explains_stale_data(platform):
    dep = _running_deployment(platform)
    platform.clock.set(platform.clock.now() + timedelta(minutes=5))
    fake = FakeClaude(
        reply(
            Block(
                "tool_use", id="t1", name="diagnose_deployment", input={"deployment_id": dep.deployment_id}
            ),
            stop="tool_use",
        ),
        reply(Block("text", text="Market data is stale.")),
    )
    copilot, _ = _copilot(platform, fake)
    principal = _principal(["QUANT_TRADER"])
    conv = copilot.start(principal)
    copilot.send(principal, conv.conversation_id, "why is it not trading?")
    checks = json.loads(fake.requests[-1]["messages"][-1]["content"][0]["content"])["checks"]
    stale = next(c for c in checks if c["check"].startswith("market data"))
    assert stale["ok"] is False and stale["detail"] == "STALE"
    set_quote(platform, BTC, "1", "2")


def test_redaction():
    assert "hunter2" not in redact("password: hunter2")
    assert redact("key jq_0123456789abcdef.SECRETpart") == "key [REDACTED]"


# ---- API ---------------------------------------------------------------------------------------


def test_ai_api_train_promote_predict(platform):
    from conftest import login_client

    client = login_client(platform)
    body = {"name": "demo", "instrument_id": BTC, "data": {"bars": 1200, "seed": 4}}
    trained = client.post("/api/v1/models:train", json=body)
    assert trained.status_code == 201, trained.text
    assert trained.json()["version"] == 1 and trained.json()["report"]["metrics"]["test"]["auc"] is not None
    for stage in ("STAGING", "SHADOW"):
        assert client.post("/api/v1/models/demo/versions/1:promote", json={"stage": stage}).status_code == 200
    blocked = client.post("/api/v1/models/demo/versions/1:promote", json={"stage": "PRODUCTION"})
    if blocked.status_code == 200:
        prediction = client.post("/api/v1/models/demo:predict", json={"instrument_id": BTC}).json()
        assert 0 <= prediction["value"] <= 1
    else:
        assert blocked.json()["code"] == "PROMOTION_BLOCKED"
    opt = client.post(
        "/api/v1/portfolio:optimize", json={"instruments": [BTC, "BINANCE:ETHUSDT"], "method": "RISK_PARITY"}
    )
    assert opt.status_code == 200 and abs(sum(opt.json()["weights"].values()) - 1) < 1e-9
    assert client.get("/api/v1/features/library").json()


def test_copilot_api_returns_503_without_claude(platform, monkeypatch):
    from conftest import login_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = login_client(platform)
    copilot = client.app.state.ctx.services["copilot"]

    def missing():
        raise RuntimeError("no credentials")

    copilot._client_factory = missing
    conv = client.post("/api/v1/copilot/conversations").json()
    resp = client.post(
        f"/api/v1/copilot/conversations/{conv['conversation_id']}/messages", json={"text": "hi"}
    )
    assert resp.status_code == 503 and resp.json()["code"] == "COPILOT_UNAVAILABLE"
