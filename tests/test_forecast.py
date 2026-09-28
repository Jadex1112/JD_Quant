"""Kronos forecasts: path summaries, the forward scorecard, the API and the paper-only strategy."""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import BTC, T0

from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.forecast.service import ForecastService, forecast_dependencies, summarize
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform


class FakeKronos:
    name = "fake-kronos"

    def __init__(self, ups=12, count_seen=None):
        self.ups, self.calls = ups, []

    def paths(self, history, horizon, count, seed):
        self.calls.append((len(history), horizon, count, seed))
        last = float(history[-1].close)
        return [
            [last * (1 + (0.01 if i < self.ups else -0.01) * (k + 1) / horizon) for k in range(horizon)]
            for i in range(count)
        ]


def test_summary_reports_the_spread_of_paths():
    paths = [[100, 101, 102], [100, 99, 98], [100, 102, 104], [100, 100, 101]]
    s = summarize(100.0, paths)
    assert s["prob_up"] == 0.75 and s["p50"] == pytest.approx(101.5)
    assert s["p10"] < s["p50"] < s["p90"] and len(s["band"]) == 3


def _service(clock, ups=12):
    platform = build_paper_platform(clock)
    instrument = platform.instruments.get(BTC)
    candles = random_walk_candles(instrument, T0 - timedelta(hours=300), 400, start_price=Decimal(50000))

    def history(inst, interval_seconds, bars):
        now = clock.now()
        return [c for c in candles if c.close_ts <= now][-bars:], "broker history"

    fake = FakeKronos(ups)
    return ForecastService(
        platform, Store(":memory:"), history=history, predictor_factory=lambda m: fake
    ), fake


def test_forecasts_are_recorded_and_scored_forward():
    clock = SimulatedClock(T0)
    service, fake = _service(clock)
    first = service.forecast(BTC, interval_seconds=3600, horizon=6)
    assert first["prob_up"] == 0.75 and first["paths_count"] == 16 and first["status"] == "PENDING"
    assert fake.calls[0][1:3] == (6, 16)
    assert first["target_time"] == (T0 + timedelta(hours=6)).isoformat()
    assert "pre-trained" in first["caveat"]
    assert service.scorecard()["scored"] == 0 and service.scorecard()["pending"] == 1
    clock.set(T0 + timedelta(hours=7))
    assert service.score_due() == 1
    [done] = service.list()
    assert done["status"] == "SCORED" and done["outcome"]["direction_hit"] in (True, False)
    card = service.scorecard()
    assert card["scored"] == 1 and "too few" in card["verdict"]
    assert 0 <= card["brier"] <= 1


def test_forecast_needs_history_and_dependencies_are_reported():
    clock = SimulatedClock(T0 - timedelta(hours=260))  # only 40 bars exist yet
    service, _ = _service(clock)
    with pytest.raises(PlatformError, match="60 bars"):
        service.forecast(BTC)
    assert service.status()["available"] is True  # a supplied predictor needs no PyTorch
    problem = forecast_dependencies()
    assert problem is None or "jdquant[forecast]" in problem


def test_kronos_strategy_refuses_backtests():
    platform = build_paper_platform(SimulatedClock(T0))
    instrument = platform.instruments.get(BTC)
    candles = random_walk_candles(instrument, T0, 100, start_price=Decimal(50000))
    with pytest.raises(PlatformError, match="paper or live"):
        run_backtest(
            BacktestConfig(strategy="kronos_forecast", instruments=[instrument], candles={BTC: candles})
        )


def test_forecast_api_and_paper_strategy(app_ctx):
    client, c, platform, clock = app_ctx
    fake = FakeKronos(ups=14)
    service = c.services["forecasts"]
    service._custom, service._factory, service._predictors = True, (lambda m: fake), {}
    status = client.get("/api/v1/forecasts/status").json()
    assert status["available"] and status["model"] == "kronos-small"
    out = client.post("/api/v1/forecasts", json={"instrument_id": BTC, "horizon": 4})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["history_source"].startswith("synthetic") and body["prob_up"] == 0.875
    assert client.get("/api/v1/forecasts").json()["forecasts"] == []  # synthetic runs are not scored
    assert client.put("/api/v1/forecasts/settings", json={"paths": 8}).json()["paths"] == 8
    assert client.put("/api/v1/forecasts/settings", json={"model": "gpt"}).status_code == 400

    # the strategy trades on a paper account through the runner's forecast hook
    runner = c.services["runner"]
    dep = platform.trading.create_deployment(
        strategy_name="kronos_forecast",
        strategy_version="1",
        account_id="paper-main",
        parameters={"quantity": "0.01", "horizon": 4},
        instruments=[BTC],
        created_by="u",
    )
    platform.trading.approve(dep.deployment_id, "u")
    platform.trading.start(dep.deployment_id)
    runner.sync(dep)
    hosted = runner.hosted[dep.deployment_id]
    assert hosted.host.ctx.forecast_source is not None
    bars = random_walk_candles(platform.instruments.get(BTC), T0, 80, start_price=Decimal(50000))
    for bar in bars[:-1]:
        hosted.history.append(bar)
    hosted.host.ctx.forecast_source = lambda iid, candles, interval, horizon: service.forecast_candles(
        iid, candles, interval_seconds=interval, horizon=horizon, record=False
    )
    hosted.history.append(bars[-1])
    hosted.host.strategy.on_bar(bars[-1])
    assert platform.oms.list_orders(account_id="paper-main")[-1].tags["reasons"] == "KRONOS_UP"
