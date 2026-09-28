"""The strategy lab: rules written as data, AI translation, backtests with confidence, paper trading."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_autopilot import NOISE, TREND, Harness, nse

from jdquant.ai.analyst import ChatModel
from jdquant.ai.prompts import STRATEGY_BUILDER, STRATEGY_REVIEW
from jdquant.autopilot.lab import StrategyLab
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.records import Candle
from jdquant.strategy.rules import describe, holds, validate_spec, value
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount

EMA_CROSS = {
    "name": "EMA trend",
    "long_entry": {
        "left": {"ind": "ema", "period": 10},
        "op": "crosses_above",
        "right": {"ind": "ema", "period": 30},
    },
    "long_exit": {
        "left": {"ind": "ema", "period": 10},
        "op": "crosses_below",
        "right": {"ind": "ema", "period": 30},
    },
    "stop_loss_pct": 8,
}


def _candles(prices, start=datetime(2026, 1, 5, tzinfo=UTC), step=timedelta(hours=1), instrument="NSE:X-EQ"):
    out = []
    for k, p in enumerate(prices):
        p = Decimal(str(p))
        opened = start + step * k
        out.append(
            Candle(
                instrument, int(step.total_seconds()), opened, opened + step, p, p + 1, p - 1, p, Decimal(100)
            )
        )
    return out


# ---- rules ---------------------------------------------------------------------------------------


def test_valid_rules_are_normalised_and_described():
    spec = validate_spec(EMA_CROSS)
    assert spec["long_entry"]["left"] == {"ind": "ema", "period": 10, "offset": 0, "mult": 1.0}
    assert spec["size_pct"] == 100 and spec["take_profit_pct"] is None and spec["session"] is None
    lines = describe(spec)
    assert lines[0] == "Buy when EMA(10) crosses above EMA(30)."
    assert lines[1] == "Sell the long when EMA(10) crosses below EMA(30)."
    assert "stop-loss 8%" in lines[2]


def test_every_problem_is_reported():
    with pytest.raises(ValidationError) as err:
        validate_spec(
            {
                "long_entry": {
                    "all": [
                        {"left": {"ind": "ichimoku"}, "op": ">", "right": {"value": 1}},
                        {
                            "left": {"ind": "macd", "fast": 30, "slow": 20},
                            "op": "above",
                            "right": {"value": 0},
                        },
                        {"left": {"ind": "rsi", "period": 1000}, "op": "<", "right": {"value": "x"}},
                    ]
                },
                "stop_loss_pct": 90,
                "session": {"tz": "Mars/Olympus", "start": "07:00", "end": "11:00"},
            }
        )
    fields = {p["field"] for p in err.value.details}
    assert "long_entry.all[0].left" in fields and "long_entry.all[1]" in fields
    assert "long_entry.all[2].left.period" in fields and "long_entry.all[2].right" in fields
    assert "stop_loss_pct" in fields and "session" in fields
    with pytest.raises(ValidationError):
        validate_spec({"long_exit": EMA_CROSS["long_exit"]})  # no entry rule at all


def test_conditions_evaluate_on_closed_bars():
    candles = _candles([100] * 30 + [101, 102, 103, 104, 110])
    breakout = validate_spec(
        {
            "long_entry": {
                "left": {"ind": "close"},
                "op": ">",
                "right": {"ind": "highest", "period": 20, "source": "high", "offset": 1},
            }
        }
    )["long_entry"]
    assert holds(breakout, candles) and not holds(breakout, candles[:30])
    rising = validate_spec({"long_entry": {"left": {"ind": "close"}, "op": "rising", "bars": 3}})[
        "long_entry"
    ]
    assert holds(rising, candles)
    cross = validate_spec(
        {
            "long_entry": {
                "left": {"ind": "close"},
                "op": "crosses_above",
                "right": {"ind": "sma", "period": 5},
            }
        }
    )["long_entry"]
    assert holds(cross, candles[:31]) and not holds(cross, candles[:33])  # crossed once, then stayed above
    assert value({"ind": "roc", "period": 1, "offset": 0, "mult": 1}, candles) == pytest.approx(
        110 / 104 * 100 - 100
    )
    band = {"ind": "ema", "period": 5, "offset": 0, "mult": 1.02}
    assert value(band, candles) > value({**band, "mult": 1}, candles)


def test_rule_strategy_trades_both_sides_with_stops():
    from jdquant.platform import fx_instrument

    eur = fx_instrument("EUR_USD")
    up = [1.10] * 40 + [1.10 + 0.001 * k for k in range(1, 80)]
    down = [up[-1] - 0.001 * k for k in range(1, 80)]
    tight = Decimal("0.0005")
    candles = [
        Candle(
            eur.instrument_id,
            c.interval_seconds,
            c.open_ts,
            c.close_ts,
            c.open,
            c.close + tight,
            c.close - tight,
            c.close,
            c.volume,
        )
        for c in _candles(up + down, instrument=eur.instrument_id)
    ]
    spec = {
        **EMA_CROSS,
        "short_entry": {
            "left": {"ind": "ema", "period": 10},
            "op": "crosses_below",
            "right": {"ind": "ema", "period": 30},
        },
        "short_exit": EMA_CROSS["long_entry"],
        "stop_loss_pct": 5,
    }
    params = {"spec": json.dumps(spec), "capital": "10000"}
    result = run_backtest(
        BacktestConfig(
            "rules", [eur], {eur.instrument_id: candles}, parameters=params, initial_capital=Decimal(10000)
        )
    )
    fills = result.fills
    assert fills[0].side.value == "BUY" and fills[1].side.value == "SELL"  # long the rise ...
    assert fills[1].quantity > fills[0].quantity  # ... then one order closes it and goes short
    assert result.final_equity > Decimal(10000)


# ---- the lab -------------------------------------------------------------------------------------


class LabChat(ChatModel):
    provider, model = "Fake", "lab-1"

    def __init__(self, specs):
        self.specs, self.calls = list(specs), []

    def ask(self, template, payload, **kw):
        self.calls.append((template, payload))
        if template is STRATEGY_BUILDER:
            spec = self.specs.pop(0)
            return json.dumps(
                {"spec": spec, "assumptions": ["EMA periods 10 and 30"], "unsupported": ["news"]}
            )
        assert template is STRATEGY_REVIEW
        return json.dumps(
            {
                "summary": "It followed the trend.",
                "strengths": ["trend"],
                "weaknesses": ["few trades"],
                "suggestions": ["try a 20/60 pair"],
            }
        )


def _lab(specs=(EMA_CROSS,)):
    h = Harness()
    chat = LabChat(specs)
    return h, StrategyLab(h.platform, h.platform.store, h.autopilot, chat, h.runner), chat


def test_translate_validates_and_repairs_once():
    bad = {"long_entry": {"left": {"ind": "ichimoku"}, "op": ">", "right": {"value": 1}}}
    h, lab, chat = _lab([bad, EMA_CROSS])
    out = lab.translate("buy when the fast EMA crosses the slow one", TREND, 86400)
    assert out["description"][0] == "Buy when EMA(10) crosses above EMA(30)."
    assert out["assumptions"] == ["EMA periods 10 and 30"] and out["unsupported"] == ["news"]
    repair = chat.calls[1][1]
    assert repair["previous"] == bad and "unknown indicator" in repair["problems"][0]["message"]
    h2, lab2, _ = _lab([bad, bad])
    with pytest.raises(ValidationError):
        lab2.translate("nonsense", TREND, 86400)
    lab2.chat = None
    with pytest.raises(PlatformError, match="AI_UNAVAILABLE"):
        lab2.translate("anything", TREND, 86400)


def test_backtest_reports_results_confidence_and_an_ai_review():
    h, lab, chat = _lab()
    run = lab.backtest(
        EMA_CROSS,
        TREND,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(1),
        user_id="u1",
        text="ema cross",
    )
    r = run.results
    assert run.data_source == "broker history" and r["trades"] > 0 and r["return"] > 0
    assert r["benchmark_return"] > 0 and len(r["periods"]) == 4 and r["charges"] > 0
    c = run.confidence
    assert 0 <= c["score"] <= 100 and c["trials"] == 1 and c["grade"] in ("High", "Medium", "Low")
    assert {x["name"] for x in c["checks"]} >= {"Enough trades", "Beat buying and holding"}
    assert c["probability_of_profit"] is None or 0 <= c["probability_of_profit"] <= 1
    assert run.review["summary"] == "It followed the trend." and run.model == "Fake · lab-1"
    assert run.equity[0][1] == 1.0 and run.trades
    assert lab.get(run.run_id).results == r  # persisted
    # Trying variations on the same market lowers the confidence of each.
    again = lab.backtest(
        EMA_CROSS,
        TREND,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(1),
        user_id="u1",
    )
    assert again.confidence["trials"] == 2 and again.confidence["score"] <= c["score"]
    other_user = lab.backtest(
        EMA_CROSS,
        TREND,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(1),
        user_id="u2",
    )
    assert other_user.confidence["trials"] == 1


def test_synthetic_data_never_earns_high_confidence():
    h, lab, _ = _lab()
    h.series = {}
    run = lab.backtest(
        EMA_CROSS,
        NOISE,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(1),
        user_id="u1",
    )
    assert run.data_source == "synthetic demo data" and run.confidence["grade"] == "Low"
    assert run.confidence["synthetic"] is True


def test_leverage_is_capped_by_the_market():
    h, lab, _ = _lab()
    run = lab.backtest(
        EMA_CROSS,
        TREND,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(10),
        user_id="u1",
    )
    assert run.leverage == "1" and run.results["leverage_cap"] == "1"  # NSE delivery: no leverage


def test_paper_trading_a_tested_strategy():
    h, lab, _ = _lab()
    run = lab.backtest(
        EMA_CROSS,
        TREND,
        interval_seconds=86400,
        bars=700,
        capital=Decimal(500000),
        leverage=Decimal(1),
        user_id="u1",
    )
    deployment = lab.paper_trade(run.run_id, "paper-main", "u1")
    assert deployment.strategy_name == "rules" and deployment.state is DeploymentState.RUNNING
    assert json.loads(deployment.parameters["spec"])["name"] == "EMA trend"
    assert (
        deployment.deployment_id in h.runner.hosted
        and lab.get(run.run_id).deployment_id == deployment.deployment_id
    )
    h.platform.trading.register_account(TradingAccount("fyers-1", "Fyers", "FYERS", AccountMode.LIVE, "INR"))
    with pytest.raises(PlatformError, match="PAPER_ONLY"):
        lab.paper_trade(run.run_id, "fyers-1", "u1")


def test_lab_api(platform):
    from fastapi.testclient import TestClient
    from test_autopilot import OWNER

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    platform.instruments.add(nse("TREND-EQ"))
    context = build_context(Settings(), platform)
    context.services["lab"].chat = LabChat([EMA_CROSS])
    client = TestClient(create_app(context=context))
    client.post("/api/v1/setup", json=OWNER)
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    translated = client.post(
        "/api/v1/strategy-lab/translate", json={"text": "ema cross please", "instrument_id": TREND}
    ).json()
    assert translated["spec"]["name"] == "EMA trend"
    created = client.post(
        "/api/v1/strategy-lab/backtests",
        json={
            "spec": translated["spec"],
            "instrument_id": TREND,
            "interval_seconds": 86400,
            "bars": 500,
            "capital": "300000",
            "text": "ema cross please",
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["data_source"] == "synthetic demo data" and run["confidence"]["grade"] == "Low"
    listed = client.get("/api/v1/strategy-lab/runs").json()
    assert listed[0]["run_id"] == run["run_id"] and "equity" not in listed[0]
    paper = client.post(f"/api/v1/strategy-lab/runs/{run['run_id']}:paper-trade", json={})
    assert paper.status_code == 201 and paper.json()["state"] == "RUNNING"
    bad = client.post("/api/v1/strategy-lab/backtests", json={"spec": {"x": 1}, "instrument_id": TREND})
    assert bad.status_code in (400, 422) and bad.json()["code"] == "RULES_INVALID"


def test_backtest_endpoint_uses_broker_history_and_market_charges(platform):
    from fastapi.testclient import TestClient
    from test_autopilot import OWNER

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    context = build_context(Settings(), platform)
    client = TestClient(create_app(context=context))
    client.post("/api/v1/setup", json=OWNER)
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    body = {
        "strategy": "ma_crossover",
        "instrument_id": "NSE:RELIANCE",
        "data": {
            "start": "2025-01-01T00:00:00Z",
            "bars": 300,
            "interval_seconds": 86400,
            "start_price": "1400",
        },
    }
    missing = client.post("/api/v1/backtests", json={**body, "data_source": "history"})
    assert missing.status_code in (400, 422) and missing.json()["code"] == "DATA_UNAVAILABLE"
    history = _candles(
        [1400 + (k % 20) * 5 for k in range(300)], instrument="NSE:RELIANCE", step=timedelta(days=1)
    )
    context.services["autopilot"]._venue_candles = lambda inst, interval, bars: history
    ok = client.post("/api/v1/backtests", json={**body, "data_source": "history", "fees_model": "market"})
    assert ok.status_code == 201, ok.text


def test_visual_builder_vocabulary_and_check(app_ctx):
    client = app_ctx[0]
    vocab = client.get("/api/v1/strategy-lab/vocabulary").json()
    assert vocab["indicators"]["ema"]["params"]["period"]["default"] == 20
    assert vocab["indicators"]["bb"]["choice"]["options"] == ["upper", "middle", "lower"]
    assert "crosses_above" in vocab["ops"]
    checked = client.post("/api/v1/strategy-lab/check", json={"spec": EMA_CROSS}).json()
    assert checked["spec"]["long_entry"] and checked["description"]
    bad = client.post("/api/v1/strategy-lab/check", json={"spec": {"long_exit": {}}})
    assert bad.status_code == 400 and bad.json()["errors"][0]["field"] == "long_entry"


def test_choose_kimi_or_nemotron_on_nvidia_and_see_the_rules_as_code():
    import httpx

    from jdquant.ai.analyst import OpenAICompatibleChat, reasoning_options

    assert reasoning_options("nvidia/nemotron-3-ultra-550b-a55b", True) == {
        "chat_template_kwargs": {"enable_thinking": True}
    }
    assert reasoning_options("moonshotai/kimi-k3", True) == {"reasoning_effort": "high"}
    assert reasoning_options("moonshotai/kimi-k2.6", False) == {"chat_template_kwargs": {"thinking": False}}
    assert reasoning_options("meta/llama-4", True) == {}

    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "moonshotai/kimi-k3"}, {"id": "nvidia/nemotron-3-ultra-550b-a55b"}]},
            )
        body = json.loads(request.content)
        requests.append(body)
        if "temperature" in body and body["model"] == "moonshotai/kimi-k3":
            return httpx.Response(400, json={"error": "temperature is fixed for this model"})
        answer = {"spec": EMA_CROSS, "assumptions": [], "unsupported": []}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {}}
        )

    h, lab, _ = _lab()
    lab.chat = OpenAICompatibleChat(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test-key",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        http=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    listed = lab.models()
    assert listed["default"] == "nvidia/nemotron-3-ultra-550b-a55b" and listed["switchable"]
    assert "moonshotai/kimi-k3" in listed["available"] and listed["presets"][1]["id"] == "moonshotai/kimi-k3"

    out = lab.translate("buy on an ema cross", TREND, 86400, model="moonshotai/kimi-k3")
    assert out["model"] == "NVIDIA · moonshotai/kimi-k3"
    first, retry = requests[-2], requests[-1]
    assert first["reasoning_effort"] == "high" and "chat_template_kwargs" not in first
    assert "temperature" not in retry and retry["model"] == "moonshotai/kimi-k3"  # retried with essentials
    assert "def long_entry(bars) -> bool:" in out["code"] and "ema(bars, period=10" in out["code"]
    compile(out["code"], "rules.py", "exec")

    lab.translate("buy on an ema cross", TREND, 86400)  # no choice: the configured Nemotron
    assert requests[-1]["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert requests[-1]["chat_template_kwargs"] == {"enable_thinking": True}
    with pytest.raises(ValidationError):
        lab.translate("buy on an ema cross", TREND, 86400, model="not a model; rm -rf")


def test_rules_as_code_endpoint(app_ctx):
    client = app_ctx[0]
    code = client.post("/api/v1/strategy-lab/code", json={"spec": EMA_CROSS}).json()["code"]
    assert "crosses" not in code.split('"""')[-1]  # crosses are spelled out as comparisons
    assert "ema(bars, period=10, ago=1) <= ema(bars, period=30, ago=1)" in code
    models = client.get("/api/v1/strategy-lab/models").json()
    assert "switchable" in models
