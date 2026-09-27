"""AI autopilot: walk-forward research, luck rejection, autonomous deploy/retire/promote, live arming."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from conftest import OWNER, T0, advance, set_quote
from test_ai import Block, FakeClaude, reply

from jdquant.ai.models import ModelRegistry
from jdquant.autopilot import research as research_module
from jdquant.autopilot.engine import PAPER_AI_ACCOUNT, Autopilot
from jdquant.autopilot.research import Candidate, ResearchConfig, fold_bounds, research
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.types import Side
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.markets.india import IST
from jdquant.oms.orders import OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.audit import AuditLog
from jdquant.strategy.runner import DeploymentRunner
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount

TREND, NOISE = "NSE:TREND-EQ", "NSE:NOISE-EQ"
START = datetime(2023, 1, 2, 10, 0, tzinfo=UTC)


def nse(symbol: str) -> Instrument:
    return Instrument(
        "NSE", symbol, AssetClass.EQUITY, symbol, "INR", Decimal("0.05"), Decimal(1), Decimal(1)
    )


def trending(bars=800, seed=5):
    return random_walk_candles(
        nse("TREND-EQ"), START, bars, interval_seconds=86400, start_price=Decimal(1000), volatility=0.012,
        drift=0.0015, seed=seed,
    )  # fmt: skip


def noise(symbol="NOISE-EQ", bars=800, seed=11):
    return random_walk_candles(
        nse(symbol), START, bars, interval_seconds=86400, start_price=Decimal(1000), volatility=0.015,
        drift=0.0, seed=seed,
    )  # fmt: skip


def path(closes):
    """Daily candles following the given closes exactly (open = previous close)."""
    from jdquant.marketdata.records import Candle

    out, prev = [], Decimal(closes[0])
    for i, close in enumerate(closes):
        close = Decimal(str(close))
        open_ts = START + timedelta(days=i)
        out.append(Candle(TREND, 86400, open_ts, open_ts + timedelta(days=1), prev, max(prev, close),
                          min(prev, close), close, Decimal(1000)))  # fmt: skip
        prev = close
    return out


# ---- research ----------------------------------------------------------------------------------


def test_research_selects_a_real_trend_and_rejects_noise():
    result = research(
        [nse("TREND-EQ"), nse("NOISE-EQ")],
        {TREND: trending(), NOISE: noise()},
        ResearchConfig(capital=Decimal(500_000)),
    )
    assert result.trials == 36
    assert [s.evaluation.instrument_id for s in result.selections] == [TREND]
    assert not any(e.passed for e in result.evaluations if e.instrument_id == NOISE)
    chosen = result.selections[0]
    assert chosen.capital == Decimal(200_000)  # capped at max_weight 40% of the budget
    assert chosen.evaluation.holdout["return"] > 0 and chosen.evaluation.dsr >= 0.75


def test_pure_noise_produces_no_selections():
    instruments = [nse(f"N{i}-EQ") for i in range(6)]
    candles = {i.instrument_id: noise(i.symbol, seed=200 + n) for n, i in enumerate(instruments)}
    result = research(instruments, candles, ResearchConfig())
    assert result.trials == 6 * 18 and result.selections == []
    assert all(not e.passed and e.reasons for e in result.evaluations)
    luck = sum(any("luck" in r for r in e.reasons) for e in result.evaluations)
    assert luck >= 0.95 * len(result.evaluations)  # the luck test alone rejects nearly all of them


def test_walk_forward_never_trains_on_the_period_it_tests(monkeypatch):
    series = trending()
    seen = []
    original = research_module.train_signal_model

    def spy(history, horizon):
        seen.append(history[-1].close_ts)
        return original(history, horizon)

    monkeypatch.setattr(research_module, "train_signal_model", spy)
    config = ResearchConfig()
    research_module.walk_forward(
        nse("TREND-EQ"), series, Candidate("ml", (("threshold", Decimal("0.05")),)), config
    )
    starts = [series[start].open_ts for start, _ in fold_bounds(len(series), config)]
    assert len(seen) == config.folds
    assert all(trained_until <= start for trained_until, start in zip(seen, starts, strict=True))


def test_short_history_is_skipped_with_a_reason():
    result = research([nse("TREND-EQ")], {TREND: trending(bars=150)}, ResearchConfig())
    assert result.evaluations == [] and "at least" in result.skipped[TREND]


# ---- autopilot controller ----------------------------------------------------------------------


class Harness:
    def __init__(self, series=None, summarizer=None):
        self.clock = SimulatedClock(T0)
        self.platform = build_paper_platform(self.clock, store=Store(":memory:"))
        for symbol in ("TREND-EQ", "NOISE-EQ"):
            self.platform.instruments.add(nse(symbol))
        self.series = series or {TREND: trending(), NOISE: noise()}
        self.audit = AuditLog(self.platform.store, self.clock)
        self.models = ModelRegistry(self.platform.store, self.clock, self.platform.bus)
        self.runner = DeploymentRunner(self.platform, lambda i: None)
        self.runner.models = self.models
        self.live_ready = True
        self.autopilot = Autopilot(
            self.platform,
            self.platform.store,
            self.audit,
            runner=self.runner,
            models=self.models,
            venue_candles=lambda inst, interval, limit: self.series.get(inst.instrument_id),
            live_ready=lambda account_id: self.live_ready,
            summarizer=summarizer,
        )
        self.autopilot.update_config(
            {"universe": [TREND, NOISE], "capital": "500000", "min_paper_days": 5, "min_paper_trades": 1},
            "tester",
        )

    def managed(self, mode="PAPER"):
        return [m for m in self.autopilot.managed.values() if m.mode == mode and m.status == "ACTIVE"]

    def kinds(self):
        return [d.kind for d in self.autopilot.decisions()]

    def paper_trade(self, managed, price="1000", qty="100"):
        set_quote(self.platform, managed.instrument_id, str(Decimal(price) - 1), str(Decimal(price) + 1))
        order = self.platform.oms.submit(
            OrderRequest(
                PAPER_AI_ACCOUNT,
                managed.instrument_id,
                Side.BUY,
                OrderType.MARKET,
                Decimal(qty),
                deployment_id=managed.deployment_id,
            )  # fmt: skip
        )
        assert order.status is OrderStatus.FILLED
        return order


def test_cycle_deploys_winners_to_the_ai_paper_account():
    h = Harness()
    run = h.autopilot.run_cycle()
    assert run.error is None and run.data_source == "broker history" and run.trials == 36
    [m] = h.managed()
    assert m.instrument_id == TREND and m.capital == Decimal(200_000)
    deployment = h.platform.trading.get_deployment(m.deployment_id)
    assert deployment.account_id == PAPER_AI_ACCOUNT and deployment.state is DeploymentState.RUNNING
    assert deployment.strategy_name == "autopilot" and deployment.created_by == "autopilot"
    assert deployment.parameters["capital"] == "200000" and deployment.bar_interval_seconds == 86400
    assert m.deployment_id in h.runner.hosted
    assert h.kinds()[:2] == ["CYCLE", "DEPLOY"]
    assert h.autopilot.runs(1)[0].selected[0]["equity"]
    actions = [e["action"] for e in [r.__dict__ for r in h.audit.search(limit=50)]]
    assert "autopilot.deploy" in actions


def _with_grid(grid, fn):
    original = research_module.candidate_grid
    research_module.candidate_grid = lambda: grid
    try:
        return fn()
    finally:
        research_module.candidate_grid = original


ML = Candidate("ml", (("threshold", Decimal("0.05")),), horizon=1)
MA = Candidate("ma_cross", (("fast", 10), ("slow", 30)))


def test_ml_winner_is_registered_and_promoted_to_production():
    from jdquant.ai.models import Stage

    h = Harness()
    h.models.min_test_auc = 0.0
    _with_grid([ML], h.autopilot.run_cycle)
    [m] = h.managed()
    assert m.signal == "ml" and m.model.startswith("ap-trend-eq-h1-")
    assert h.models.in_stage(m.model, Stage.PRODUCTION).approvals[-1]["approver"] == "autopilot"


def test_ml_blocked_by_the_model_gate_falls_back_to_the_next_strategy():
    h = Harness()
    h.models.min_test_auc = 0.99  # the final retrained model cannot pass
    _with_grid([ML, MA], h.autopilot.run_cycle)
    [m] = h.managed()
    assert m.signal == "ma_cross"
    skip = next(d for d in h.autopilot.decisions() if d.kind == "SKIP")
    assert "ML" in skip.title and "AUC" in skip.reasons[0]


def test_strategy_whose_edge_disappears_is_retired():
    h = Harness()
    h.autopilot.run_cycle()
    [m] = h.managed()
    h.series[TREND] = noise("TREND-EQ", seed=11)  # the trend is gone in fresh data
    advance(h.clock, days=1)
    h.autopilot.run_cycle()
    assert h.autopilot.managed[m.deployment_id].status == "RETIRED"
    assert h.platform.trading.get_deployment(m.deployment_id).state is DeploymentState.RETIRED
    retire = next(d for d in h.autopilot.decisions() if d.kind == "RETIRE")
    assert "no longer holds up" in retire.reasons[0]


def test_drawdown_breach_closes_the_position():
    h = Harness()
    h.autopilot.run_cycle()
    [m] = h.managed()
    h.paper_trade(m, price="1000", qty="200")  # ₹200k position
    set_quote(h.platform, TREND, "799", "801")  # -20% ≈ -₹40k, beyond 15% of capital
    h.autopilot.monitor()
    h.autopilot.monitor()
    assert h.autopilot.managed[m.deployment_id].status == "RETIRED"
    assert h.platform.positions.net_quantity(PAPER_AI_ACCOUNT, TREND) == 0
    assert "drawdown" in next(d for d in h.autopilot.decisions() if d.kind == "RETIRE").reasons[0]


def test_live_promotion_needs_arming_and_respects_the_cap():
    h = Harness()
    h.platform.trading.register_account(TradingAccount("fyers-1", "Fyers", "FYERS", AccountMode.LIVE, "INR"))
    h.autopilot.run_cycle()
    [m] = h.managed()
    h.paper_trade(m)
    advance(h.clock, days=6)
    h.autopilot.run_cycle()
    assert "READY_FOR_LIVE" in h.kinds() and not h.managed("LIVE")

    with pytest.raises(PlatformError, match="ACCOUNT_NOT_LIVE"):
        h.autopilot.arm_live(PAPER_AI_ACCOUNT, Decimal(50_000), "owner")
    h.live_ready = False
    h.autopilot.arm_live("fyers-1", Decimal(50_000), "owner")
    advance(h.clock, days=1)
    h.autopilot.run_cycle()
    assert not h.managed("LIVE") and "waits for the broker" in h.autopilot.decisions()[1].title

    h.live_ready = True
    advance(h.clock, days=1)
    h.autopilot.run_cycle()
    [live] = h.managed("LIVE")
    deployment = h.platform.trading.get_deployment(live.deployment_id)
    assert deployment.account_id == "fyers-1" and deployment.approved_by == "owner"
    assert live.capital == Decimal(50_000) and deployment.parameters["capital"] == "50000"
    assert live.source_deployment == m.deployment_id

    h.autopilot.disarm_live("owner")
    assert not h.managed("LIVE") and not h.autopilot.live.armed
    assert h.platform.trading.get_deployment(live.deployment_id).state is DeploymentState.RETIRED
    assert h.kinds()[0] == "DISARM"


def test_schedule_runs_after_each_nse_close():
    h = Harness()
    assert h.autopilot.next_run_at() is None  # disabled until enabled
    h.autopilot.update_config({"enabled": True}, "tester")
    assert h.autopilot.next_run_at() == h.clock.now()
    h.autopilot.tick()
    friday = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
    h.autopilot.last_run_at = friday
    assert h.autopilot.next_run_at() == datetime(2026, 1, 12, 10, 30, tzinfo=UTC)  # Monday 16:00 IST


def test_claude_explains_each_cycle():
    fake = FakeClaude(reply(Block("text", text="The autopilot started paper trading TREND-EQ.")))
    from jdquant.ai.copilot import Copilot
    from jdquant.autopilot.engine import make_summarizer

    h = Harness()
    copilot = Copilot(h.platform.store, h.clock, h.audit, [], client_factory=lambda: fake)
    h.autopilot._summarizer = make_summarizer(copilot)
    run = h.autopilot.run_cycle()
    assert run.summary == "The autopilot started paper trading TREND-EQ."
    request = fake.requests[0]
    assert (
        "briefing" in request["system"]
        and '"data_source": "broker history"' in request["messages"][0]["content"]
    )


def test_invalid_configuration_is_rejected():
    h = Harness()
    with pytest.raises(PlatformError, match="AUTOPILOT_CONFIG_INVALID"):
        h.autopilot.update_config({"universe": ["NSE:NOPE-EQ"]}, "tester")
    with pytest.raises(PlatformError, match="AUTOPILOT_CONFIG_INVALID"):
        h.autopilot.update_config({"max_weight": 2}, "tester")


def test_state_survives_restart(tmp_path):
    db = tmp_path / "jq.db"
    h = Harness()
    h.platform.store = Store(db)
    h.autopilot._store = h.platform.store
    h.autopilot.update_config({"universe": [TREND], "capital": "300000"}, "tester")
    again = Autopilot(build_paper_platform(SimulatedClock(T0), store=Store(db)), Store(db), h.audit)
    assert again.config.universe == [TREND] and again.config.capital == Decimal(300_000)


# ---- strategy behaviour ------------------------------------------------------------------------


def _backtest(series, **params):
    from jdquant.backtest.engine import BacktestConfig, run_backtest
    from jdquant.markets.india import IndiaEquityFees

    return run_backtest(
        BacktestConfig(
            "autopilot",
            [nse("TREND-EQ")],
            {TREND: series},
            {"signal": "ma_cross", **params},
            initial_capital=Decimal(100_000),
            base_currency="INR",
            fees=IndiaEquityFees(),
        )  # fmt: skip
    )


def test_sizing_uses_allocated_capital_in_whole_shares():
    result = _backtest(trending(bars=200), capital="50000", stop_loss="0")
    first = result.fills[0]
    assert first.quantity == int(first.quantity) and first.quantity * first.price <= Decimal(51_000)
    assert first.fee > 0  # Indian charges applied


def test_stop_loss_exits_a_losing_position():
    from jdquant.marketdata.records import Candle

    # 25 quiet bars in a wide 900-1005 range, a breakout close at 1010 (the signal enters), then a steady
    # 1%/bar fall. The breakout signal's own exit waits for a close below 900; the 3% stop must not.
    bars = []
    for i in range(45):
        close = (
            Decimal(1000)
            if i < 25
            else Decimal(1010)
            if i == 25
            else Decimal(1010) * Decimal("0.99") ** (i - 25)
        )
        open_ts = START + timedelta(days=i)
        high, low = (Decimal(1005), Decimal(900)) if i < 25 else (close + 1, close - 1)
        bars.append(Candle(TREND, 86400, open_ts, open_ts + timedelta(days=1), close, max(high, close),
                           min(low, close), close, Decimal(1000)))  # fmt: skip
    params = {"signal": "donchian", "period": 20, "capital": "50000"}
    stopped = _backtest(bars, **params, stop_loss="0.03")
    unprotected = _backtest(bars, **params, stop_loss="0")
    stop_exit = next(f for f in stopped.fills if f.side is Side.SELL)
    late_exit = next(f for f in unprotected.fills if f.side is Side.SELL)
    entry = stopped.fills[0].price
    assert stop_exit.price >= entry * Decimal("0.95")  # out within about the 3% stop plus one bar
    assert late_exit.price < entry * Decimal("0.90") and late_exit.exchange_ts > stop_exit.exchange_ts
    assert stopped.final_equity > unprotected.final_equity


def test_intraday_positions_close_before_the_cutoff():
    from jdquant.marketdata.records import Candle

    # 15-minute bars on one session, rising all day: the strategy must be flat after 15:10 IST.
    day = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)  # 09:15 IST
    bars = []
    for i in range(25):
        price = Decimal(1000 + 2 * i)
        open_ts = day + timedelta(minutes=15 * i)
        bars.append(Candle(TREND, 900, open_ts, open_ts + timedelta(minutes=15), price, price + 1, price - 1,
                           price + 1, Decimal(100)))  # fmt: skip
    result = _backtest(bars, fast=2, slow=4, capital="50000", stop_loss="0", intraday=True)
    last = result.fills[-1]
    assert last.side is Side.SELL and last.exchange_ts.astimezone(IST).time() >= time(15, 10)
    assert sum(f.quantity * f.side.sign for f in result.fills) == 0


def test_api_permissions_and_flow(platform):
    from fastapi.testclient import TestClient

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    platform.instruments.add(nse("TREND-EQ"))
    context = build_context(Settings(enforce_mfa_for_privileged=True), platform)
    client = TestClient(create_app(context=context))
    client.post("/api/v1/setup", json=OWNER)
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"

    status = client.get("/api/v1/autopilot").json()
    assert status["paper_account"] == PAPER_AI_ACCOUNT and status["latest_run"] is None
    assert client.put("/api/v1/autopilot/config", json={"universe": [TREND]}).json()["universe"] == [TREND]
    assert client.put("/api/v1/autopilot/config", json={"capital": "-5"}).status_code == 400
    armed = client.post(
        "/api/v1/autopilot/live:arm", json={"account_id": "paper-main", "capital_cap": "1000"}
    )
    assert armed.status_code == 403 and armed.json()["code"] == "MFA_ENROLLMENT_REQUIRED"
    disarm = client.post("/api/v1/autopilot/live:disarm", json={})
    assert disarm.status_code == 200  # stopping never needs extra verification

    context.services[
        "autopilot"
    ].run_cycle()  # synchronous for the test (the API starts it in the background)
    latest = client.get("/api/v1/autopilot").json()["latest_run"]
    assert latest["data_source"] == "synthetic demo data" and latest["trials"] == 18
    assert client.get("/api/v1/autopilot/decisions").json()[0]["kind"] in ("CYCLE", "DEPLOY", "SKIP")
    assert client.get(f"/api/v1/autopilot/runs/{latest['run_id']}").status_code == 200
    assert client.post("/api/v1/autopilot:run").status_code == 202
