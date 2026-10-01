"""Strategy versions through DEVELOPMENT -> BACKTEST -> VALIDATION -> PAPER -> APPROVED -> LIVE, and the
read-only AI director tools."""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import BTC, advance, market

from jdquant.ai.director_tools import build_director_tools
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.platform import PAPER_ACCOUNT_ID
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount


def _uptrend(platform):
    def candles(instrument, interval_seconds, bars):
        start = platform.clock.now() - timedelta(seconds=interval_seconds * bars)
        return random_walk_candles(
            instrument,
            start,
            bars,
            interval_seconds=interval_seconds,
            start_price=Decimal(20000),
            volatility=0.004,
            drift=0.002,
        )

    return candles


def _registry(c, platform, history=True):
    registry = c.services["registry"]
    registry._candles = _uptrend(platform) if history else (lambda *a: None)
    registry.configure({"min_validation_trades": 0, "min_paper_trades": 0, "min_paper_days": 1})
    return registry


def _create(registry, **kw):
    return registry.create(
        name="Trend",
        template="ma_crossover",
        instrument_id=BTC,
        interval_seconds=3600,
        parameters=kw or {"fast": 5, "slow": 20},
        description="",
        user="alice",
    )


def _to_approved(registry, clock, strategy_id, version):
    registry.backtest(strategy_id, version, "alice")
    assert registry.validate(strategy_id, version, "alice")["passed"]
    registry.start_paper(strategy_id, version, PAPER_ACCOUNT_ID, "alice")
    advance(clock, days=2)
    registry.approve(strategy_id, version, "bob")


def test_stages_cannot_be_skipped(app_ctx):
    _, c, platform, clock = app_ctx
    registry = _registry(c, platform, history=False)
    d = _create(registry)
    assert d.strategy_id == "STR-001" and d.versions[0].stage == "DEVELOPMENT"
    with pytest.raises(PlatformError, match="backtest it first"):
        registry.validate(d.strategy_id, "1.0", "alice")
    result = registry.backtest(d.strategy_id, "1.0", "alice")
    assert result["data"] == "synthetic (not evidence)"
    assert registry.get(d.strategy_id).versions[0].stage == "BACKTEST"
    # Validation needs real history; synthetic data is never evidence.
    with pytest.raises(PlatformError) as err:
        registry.validate(d.strategy_id, "1.0", "alice")
    assert err.value.code == "HISTORY_UNAVAILABLE"
    with pytest.raises(PlatformError) as err:
        registry.start_paper(d.strategy_id, "1.0", PAPER_ACCOUNT_ID, "alice")
    assert err.value.code == "STAGE_ORDER"
    with pytest.raises(PlatformError) as err:
        registry.go_live(d.strategy_id, "1.0", PAPER_ACCOUNT_ID, "alice")
    assert err.value.code == "STAGE_ORDER"
    with pytest.raises(PlatformError):
        registry.create(
            name="x",
            template="ai_trader",
            instrument_id=BTC,
            interval_seconds=3600,
            parameters={},
            description="",
            user="alice",
        )


def test_pipeline_to_live_new_version_and_rollback(app_ctx):
    _, c, platform, clock = app_ctx
    registry = _registry(c, platform)
    platform.trading.register_account(TradingAccount("live-1", "Live", "BINANCE", AccountMode.LIVE, "USDT"))
    d = _create(registry)
    registry.backtest(d.strategy_id, "1.0", "alice")
    validation = registry.validate(d.strategy_id, "1.0", "alice")
    assert validation["passed"], validation
    assert len(validation["periods"]) == registry.settings.validation_folds
    paper = registry.start_paper(d.strategy_id, "1.0", PAPER_ACCOUNT_ID, "alice")
    dep = platform.trading.deployments[paper["deployment_id"]]
    assert dep.state is DeploymentState.RUNNING and dep.strategy_version == "STR-001@1.0"
    with pytest.raises(PlatformError) as err:
        registry.approve(d.strategy_id, "1.0", "bob")
    assert err.value.code == "PAPER_RECORD_TOO_SHORT"
    advance(clock, days=2)
    platform.trading.single_user = False
    with pytest.raises(PlatformError) as err:
        registry.approve(d.strategy_id, "1.0", "alice")
    assert err.value.code == "FOUR_EYES"
    registry.approve(d.strategy_id, "1.0", "bob")
    with pytest.raises(PlatformError) as err:
        registry.go_live(d.strategy_id, "1.0", PAPER_ACCOUNT_ID, "bob")
    assert err.value.code == "LIVE_ACCOUNT_REQUIRED"
    first = registry.go_live(d.strategy_id, "1.0", "live-1", "bob")["deployment_id"]
    assert platform.trading.deployments[first].approved_by == "bob"
    assert registry.get(d.strategy_id).live_version == "1.0"

    registry.new_version(d.strategy_id, {"fast": 8, "slow": 30}, "slower averages", "alice")
    _to_approved(registry, clock, d.strategy_id, "1.1")
    second = registry.go_live(d.strategy_id, "1.1", "live-1", "bob")["deployment_id"]
    assert platform.trading.deployments[first].state is DeploymentState.STOPPED
    assert registry.get(d.strategy_id).live_version == "1.1"

    result = registry.rollback(d.strategy_id, "bob")
    assert result["rolled_back_from"] == "1.1" and result["to"] == "1.0"
    assert platform.trading.deployments[second].state is DeploymentState.STOPPED
    definition = registry.get(d.strategy_id)
    assert definition.live_version == "1.0"
    assert definition.version("1.1").stage == "APPROVED"
    assert [h["stage"] for h in definition.version("1.0").history][:6] == [
        "DEVELOPMENT",
        "BACKTEST",
        "VALIDATION",
        "PAPER",
        "APPROVED",
        "LIVE",
    ]

    registry.retire(d.strategy_id, "1.0", "bob")
    assert registry.get(d.strategy_id).live_version is None
    assert platform.trading.deployments[result["deployments"][0]].state is DeploymentState.STOPPED


def test_registry_api(app_ctx):
    client, c, platform, _ = app_ctx
    _registry(c, platform, history=False)
    created = client.post(
        "/api/v1/strategies",
        json={"template": "rsi_mean_reversion", "instrument_id": BTC, "interval_seconds": 3600},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["strategy_id"]
    assert created.json()["stages"][0] == "DEVELOPMENT"
    bt = client.post(f"/api/v1/strategies/{sid}/versions/1.0/backtest", json={"bars": 300})
    assert bt.status_code == 200 and bt.json()["data"].startswith("synthetic")
    no_history = client.post(f"/api/v1/strategies/{sid}/versions/1.0/validate", json={})
    assert no_history.status_code == 422 and no_history.json()["code"] == "HISTORY_UNAVAILABLE"
    early = client.post(f"/api/v1/strategies/{sid}/versions/1.0/paper", json={})
    assert early.json()["code"] == "STAGE_ORDER"
    v = client.post(f"/api/v1/strategies/{sid}/versions", json={"parameters": {}, "notes": "try"})
    assert [x["version"] for x in v.json()["versions"]] == ["1.0", "1.1"]
    listing = client.get("/api/v1/strategies").json()
    assert listing["strategies"][0]["latest_version"] == "1.1"
    assert (
        client.put("/api/v1/strategies/settings", json={"min_paper_days": 10}).json()["min_paper_days"] == 10
    )
    assert client.get("/api/v1/strategies/STR-999").status_code == 404
    retired = client.post(f"/api/v1/strategies/{sid}/versions/1.1/retire")
    assert retired.json()["versions"][1]["stage"] == "RETIRED"


def test_director_tools_explain_the_bot(app_ctx):
    _, c, platform, clock = app_ctx
    tools = {t.name: t for t in build_director_tools(c)}
    assert all(t.effect.value == "READ_ONLY" for t in tools.values())
    assert tools["explain_trade"].handler(None, {})["message"]
    platform.oms.submit(market(Side.BUY, "0.1"))
    advance(clock, minutes=3)
    platform.oms.submit(market(Side.SELL, "0.1"))
    explained = tools["explain_trade"].handler(None, {})
    assert explained["trade"]["instrument_id"] == BTC and explained["execution"]
    drawdowns = tools["strategy_drawdowns"].handler(None, {})
    assert drawdowns["strategies"][0]["trades"] == 1
    status = tools["bot_status"].handler(None, {})
    assert status["circuit_breakers"]["settings"]
    assert "disclaimer" in tools["market_events"].handler(None, {"limit": 5})
    assert tools["execution_quality"].handler(None, {})["summary"]["orders"] >= 2
    assert tools["strategy_pipeline"].handler(None, {})["strategies"] == []
    assert "summary" in tools["explain_signal"].handler(None, {})
    assert "active" in tools["wall_history"].handler(None, {"instrument_id": BTC})


def test_lab_rules_join_the_pipeline(app_ctx):
    from types import SimpleNamespace

    from test_lab import EMA_CROSS

    _, c, platform, _ = app_ctx
    registry = _registry(c, platform, history=False)
    run = SimpleNamespace(
        spec=EMA_CROSS,
        capital_quote="",
        leverage="2",
        instrument_id=BTC,
        interval_seconds=3600,
        text="EMA trend",
    )
    lab = SimpleNamespace(get=lambda run_id: run)
    d = registry.from_lab(lab, "LAB-1", name="", user="alice")
    assert d.template == "rules" and d.name == "Lab LAB-1"
    params = d.versions[0].parameters
    assert params["capital"] == "100000" and params["leverage"] == "2"
    assert registry.backtest(d.strategy_id, "1.0", "alice")["bars"] == 2000
