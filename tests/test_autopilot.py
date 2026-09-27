"""AI autopilot: walk-forward research, luck rejection, autonomous deploy/retire/promote, live arming."""

import json
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
from jdquant.marketdata.records import Candle
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.markets.india import IST
from jdquant.oms.orders import OrderRequest, OrderStatus, OrderType
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.audit import AuditLog
from jdquant.strategy.runner import DeploymentRunner
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount

TREND, NOISE = "NSE:TREND-EQ", "NSE:NOISE-EQ"
GRID = 29  # daily-bar candidates per stock
START = datetime(2023, 1, 2, 10, 0, tzinfo=UTC)


def nse(symbol: str) -> Instrument:
    return Instrument(
        "NSE", symbol, AssetClass.EQUITY, symbol, "INR", Decimal("0.05"), Decimal(1), Decimal(1)
    )


def trending(bars=800, seed=5):
    return random_walk_candles(
        nse("TREND-EQ"),
        START,
        bars,
        interval_seconds=86400,
        start_price=Decimal(1000),
        volatility=0.012,
        drift=0.0015,
        seed=seed,
    )


def noise(symbol="NOISE-EQ", bars=800, seed=11):
    return random_walk_candles(
        nse(symbol),
        START,
        bars,
        interval_seconds=86400,
        start_price=Decimal(1000),
        volatility=0.015,
        drift=0.0,
        seed=seed,
    )


def path(closes):
    """Daily candles following the given closes exactly (open = previous close)."""
    from jdquant.marketdata.records import Candle

    out, prev = [], Decimal(closes[0])
    for i, close in enumerate(closes):
        close = Decimal(str(close))
        open_ts = START + timedelta(days=i)
        out.append(
            Candle(
                TREND,
                86400,
                open_ts,
                open_ts + timedelta(days=1),
                prev,
                max(prev, close),
                min(prev, close),
                close,
                Decimal(1000),
            )
        )
        prev = close
    return out


# ---- research ----------------------------------------------------------------------------------


def test_research_selects_a_real_trend_and_rejects_noise():
    result = research(
        [nse("TREND-EQ"), nse("NOISE-EQ")],
        {TREND: trending(), NOISE: noise()},
        ResearchConfig(capital=Decimal(500_000)),
    )
    assert result.trials == 2 * GRID
    assert [s.evaluation.instrument_id for s in result.selections] == [TREND]
    assert not any(e.passed for e in result.evaluations if e.instrument_id == NOISE)
    chosen = result.selections[0]
    assert chosen.capital == Decimal(200_000)  # capped at max_weight 40% of the budget
    assert chosen.evaluation.holdout["return"] > 0 and chosen.evaluation.dsr >= 0.75


def test_pure_noise_produces_no_selections():
    instruments = [nse(f"N{i}-EQ") for i in range(6)]
    candles = {i.instrument_id: noise(i.symbol, seed=200 + n) for n, i in enumerate(instruments)}
    result = research(instruments, candles, ResearchConfig())
    assert result.trials == 6 * GRID + 3 and result.selections == []  # + 3 basket rotations
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
    def __init__(self, series=None, analyst=None):
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
            analyst=analyst,
        )
        self.autopilot.update_config(
            {
                "universe": [TREND, NOISE],
                "capital": "500000",
                "min_paper_days": 5,
                "min_paper_trades": 1,
                "max_participation": 0,
            },
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
            )
        )
        assert order.status is OrderStatus.FILLED
        return order


def test_cycle_deploys_winners_to_the_ai_paper_account():
    h = Harness()
    run = h.autopilot.run_cycle()
    assert run.error is None and run.data_source == "broker history" and run.trials == 2 * GRID
    [m] = h.managed()
    assert m.instrument_id == TREND and m.capital == Decimal(200_000)
    deployment = h.platform.trading.get_deployment(m.deployment_id)
    assert deployment.account_id == PAPER_AI_ACCOUNT and deployment.state is DeploymentState.RUNNING
    assert deployment.strategy_name == "autopilot" and deployment.created_by == "autopilot"
    assert Decimal(deployment.parameters["capital"]) == 200000 and deployment.bar_interval_seconds == 86400
    assert m.deployment_id in h.runner.hosted
    assert h.kinds()[:2] == ["CYCLE", "DEPLOY"]
    assert h.autopilot.runs(1)[0].selected[0]["equity"]
    actions = [e["action"] for e in [r.__dict__ for r in h.audit.search(limit=50)]]
    assert "autopilot.deploy" in actions


def _with_grid(grid, fn):
    original = research_module.candidate_grid
    research_module.candidate_grid = lambda interval_seconds=86400: grid
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
    assert live.capital == Decimal(50_000) and Decimal(deployment.parameters["capital"]) == 50000
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


def test_claude_reviews_each_cycle():
    concern = {"instrument_id": "NSE:TREND-EQ", "concern": "few trades", "severity": "low"}
    answer = json.dumps({"summary": "Paper trading TREND-EQ.", "concerns": [concern]})
    fake = FakeClaude(reply(Block("text", text=answer)))
    from jdquant.ai.analyst import claude_analyst
    from jdquant.ai.copilot import Copilot

    h = Harness()
    copilot = Copilot(h.platform.store, h.clock, h.audit, [], client_factory=lambda: fake)
    h.autopilot._analyst = claude_analyst(copilot)
    run = h.autopilot.run_cycle()
    assert run.summary == "Paper trading TREND-EQ." and run.analyst.startswith("Claude")
    assert run.concerns == [{"instrument_id": "NSE:TREND-EQ", "concern": "few trades", "severity": "low"}]
    request = fake.requests[0]
    assert "risk analyst" in request["system"]
    assert '"data_source": "broker history"' in request["messages"][0]["content"]
    assert h.managed()  # a low-severity concern never blocks anything


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
        )
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
        bars.append(
            Candle(
                TREND,
                86400,
                open_ts,
                open_ts + timedelta(days=1),
                close,
                max(high, close),
                min(low, close),
                close,
                Decimal(1000),
            )
        )
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
        bars.append(
            Candle(
                TREND,
                900,
                open_ts,
                open_ts + timedelta(minutes=15),
                price,
                price + 1,
                price - 1,
                price + 1,
                Decimal(100),
            )
        )
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
    assert latest["data_source"] == "synthetic demo data" and latest["trials"] == GRID
    assert client.get("/api/v1/autopilot/decisions").json()[0]["kind"] in ("CYCLE", "DEPLOY", "SKIP")
    assert client.get(f"/api/v1/autopilot/runs/{latest['run_id']}").status_code == 200
    assert client.post("/api/v1/autopilot:run").status_code == 202


# ---- the wider strategy set --------------------------------------------------------------------


def test_candidate_grid_depends_on_bar_size():
    from jdquant.autopilot.research import candidate_grid

    daily, fifteen = candidate_grid(86400), candidate_grid(900)
    assert len(daily) == GRID and not any(c.signal == "orb" for c in daily)
    orb = [c for c in fifteen if c.signal == "orb"]
    assert [dict(c.params)["period"] for c in orb] == [2, 4]  # first 30 and 60 minutes
    assert len({c.key for c in daily}) == len(daily)  # keys are unique
    assert all(c.label != c.key for c in fifteen)  # every candidate has a readable label


@pytest.mark.parametrize("signal", ["momentum", "macd", "supertrend", "rsi_trend", "volume_breakout"])
def test_new_signals_trade_without_errors(signal):
    from jdquant.autopilot.research import candidate_grid, strategy_parameters

    candidate = next(c for c in candidate_grid() if c.signal == signal)
    params = strategy_parameters(candidate, Decimal(100_000), ResearchConfig())
    result = _backtest(trending(), **params)
    assert result.strategy_errors == 0
    assert result.fills, f"{signal} never traded on a trending series"


def test_trend_signals_are_long_in_an_uptrend_and_flat_in_a_downtrend():
    from jdquant.strategy.indicators import macd, rate_of_change, supertrend

    up = [100 + i for i in range(80)]
    down = list(reversed(up))
    assert supertrend([x + 1 for x in up], [x - 1 for x in up], up, 10, 3)[0] is True
    assert supertrend([x + 1 for x in down], [x - 1 for x in down], down, 10, 3)[0] is False
    line, signal_line = macd(up[:40] + down[:20])  # a peak followed by a fall
    assert line < signal_line
    assert rate_of_change(up, 20) > 0 > rate_of_change(down, 20)


def test_trailing_stop_locks_in_gains():
    # Breakout entry at 1010, a run-up to about 1300, then a steady fall.
    rise = [1010 * 1.02**k for k in range(14)]
    closes = [1000] * 25 + rise + [rise[-1] * 0.98**k for k in range(1, 25)]
    bars = []
    for i, close in enumerate(closes):
        close = Decimal(str(round(close, 2)))
        open_ts = START + timedelta(days=i)
        high, low = (Decimal(1005), Decimal(900)) if i < 25 else (close + 1, close - 1)
        bars.append(
            Candle(
                TREND,
                86400,
                open_ts,
                open_ts + timedelta(days=1),
                close,
                max(high, close),
                min(low, close),
                close,
                Decimal(1000),
            )
        )
    base = {"signal": "donchian", "period": 20, "capital": "50000", "stop_loss": "0.08"}
    trailing = _backtest(bars, **base, trailing_stop="0.05")
    fixed = _backtest(bars, **base)
    trailing_exit = next(f for f in trailing.fills if f.side is Side.SELL)
    fixed_exit = next(f for f in fixed.fills if f.side is Side.SELL)
    assert trailing_exit.price > Decimal(1200)  # sold within about 5% of the ~1300 high
    assert trailing_exit.exchange_ts < fixed_exit.exchange_ts
    assert trailing.final_equity > fixed.final_equity


def test_opening_range_breakout_trades_the_break_and_exits_by_the_cutoff():
    day = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)  # 09:15 IST, 15-minute bars
    prices = [1000, 1004, 1002, 1003, 1010, 1015, 1020] + [1020] * 18
    bars = []
    for i, price in enumerate(prices):
        price = Decimal(price)
        open_ts = day + timedelta(minutes=15 * i)
        bars.append(
            Candle(
                TREND,
                900,
                open_ts,
                open_ts + timedelta(minutes=15),
                price,
                price + 1,
                price - 1,
                price,
                Decimal(100),
            )
        )
    result = _backtest(bars, signal="orb", period=2, capital="50000", stop_loss="0", intraday=True)
    entry = next(f for f in result.fills if f.side is Side.BUY)
    assert entry.exchange_ts.astimezone(IST).time() >= time(10, 15)  # after the range (09:15-09:45) broke
    exit_ = result.fills[-1]
    assert exit_.side is Side.SELL and exit_.exchange_ts.astimezone(IST).time() >= time(15, 10)


def test_resting_orders_on_the_ai_paper_account_fill_from_live_quotes():
    from jdquant.connectivity.poller import VenuePoller
    from jdquant.marketdata.records import Quote

    h = Harness()
    poller = VenuePoller(h.platform, type("NoVenues", (), {"connections": {}, "adapters": {}})(), h.runner)
    set_quote(h.platform, TREND, "999", "1001")
    order = h.platform.oms.submit(
        OrderRequest(PAPER_AI_ACCOUNT, TREND, Side.BUY, OrderType.LIMIT, Decimal(5), limit_price=Decimal(990))
    )
    assert order.status is OrderStatus.OPEN
    poller.on_quote(Quote(TREND, h.clock.now(), Decimal("985"), Decimal(1), Decimal("987"), Decimal(1)))
    assert order.status is OrderStatus.FILLED


def test_liquidity_filter_rejects_positions_too_big_for_the_market():
    thin = [
        c.__class__(
            c.instrument_id,
            c.interval_seconds,
            c.open_ts,
            c.close_ts,
            c.open,
            c.high,
            c.low,
            c.close,
            Decimal(10),
        )
        for c in trending()
    ]  # about ₹10k-30k traded a day
    config = ResearchConfig(capital=Decimal(500_000), max_participation=0.01)
    result = research([nse("TREND-EQ")], {TREND: thin}, config)
    assert result.selections == []
    assert all(any("too illiquid" in r for r in e.reasons) for e in result.evaluations)


# ---- hedge-fund style behaviour and multi-asset --------------------------------------------------


def gold_future(symbol="GOLDM26JANFUT", days=900):
    return Instrument(
        "MCX",
        symbol,
        AssetClass.COMMODITY,
        "GOLDM",
        "INR",
        Decimal(1),
        Decimal(1),
        Decimal(1),
        contract_multiplier=Decimal(10),
        expiry=START + timedelta(days=days),
        underlying="GOLDM",
    )


def test_futures_trend_strategies_sell_short_in_a_downtrend():
    from jdquant.autopilot.research import candidate_grid, strategy_parameters
    from jdquant.backtest.engine import BacktestConfig, run_backtest
    from jdquant.markets.india import MCX_FEES

    gold = gold_future()
    falling = random_walk_candles(
        gold,
        START,
        400,
        interval_seconds=86400,
        start_price=Decimal(60000),
        volatility=0.008,
        drift=-0.003,
        seed=4,
    )
    candidate = next(c for c in candidate_grid() if c.signal == "ewmac")
    params = strategy_parameters(candidate, Decimal(500_000), ResearchConfig(), gold)
    assert params["allow_short"] is True
    result = run_backtest(
        BacktestConfig(
            "autopilot",
            [gold],
            {gold.instrument_id: falling},
            params,
            initial_capital=Decimal(500_000),
            base_currency="INR",
            fees=MCX_FEES,
        )
    )
    assert result.fills[0].side is Side.SELL  # opened short
    assert result.final_equity > Decimal(500_000)
    # Stocks never short, even with the same signal.
    assert strategy_parameters(candidate, Decimal(1), ResearchConfig(), nse("X-EQ"))["allow_short"] is False


def test_volatility_target_shrinks_positions_in_wild_markets():
    from jdquant.autopilot.strategy import volatility_scale

    def path(step):  # alternating up/down moves of `step`
        prices = [100.0]
        for i in range(80):
            prices.append(prices[-1] * (1 + step if i % 2 else 1 - step))
        return prices

    calm, wild = path(0.001), path(0.04)  # ~1.6% and ~63% annualized volatility
    assert volatility_scale(calm, Decimal("0.2"), 248) == 1  # never levered up
    assert volatility_scale(wild, Decimal("0.2"), 248) < Decimal("0.5")
    assert volatility_scale(wild, Decimal(0), 248) == 1  # disabled


def test_ewmac_forecast_follows_the_trend_and_is_capped():
    from jdquant.strategy.indicators import ewmac_forecast

    up = [100 * 1.004**i for i in range(200)]
    assert 0 < ewmac_forecast(up, 8) <= 20
    assert -20 <= ewmac_forecast(list(reversed(up)), 8) < 0


def test_rotation_holds_the_leaders_and_rebalances():
    from jdquant.backtest.engine import BacktestConfig, run_backtest

    names = ["LEAD-EQ", "MID-EQ", "LAG-EQ", "DOWN-EQ"]
    drifts = [0.004, 0.001, 0.0, -0.003]
    members = [nse(n) for n in names]
    candles = {
        m.instrument_id: random_walk_candles(
            m, START, 400, interval_seconds=86400, start_price=Decimal(500), volatility=0.01, drift=d, seed=i
        )
        for i, (m, d) in enumerate(zip(members, drifts, strict=True))
    }
    params = {"mode": "momentum", "lookback": 63, "skip": 5, "hold": 1, "rebalance": 10, "capital": "100000"}
    result = run_backtest(
        BacktestConfig(
            "rotation", members, candles, params, initial_capital=Decimal(100_000), base_currency="INR"
        )
    )
    bought = {f.instrument_id for f in result.fills if f.side is Side.BUY}
    assert "NSE:LEAD-EQ" in bought and "NSE:DOWN-EQ" not in bought  # never buys a falling name
    assert result.strategy_errors == 0 and result.final_equity > Decimal(100_000)


def test_continuous_futures_resolve_to_the_front_month_and_roll():
    h = Harness(series={})
    near, far = gold_future("GOLDM26JANFUT", days=0), gold_future("GOLDM26FEBFUT", days=0)
    near = near.__class__(**{**near.__dict__, "expiry": T0 + timedelta(days=20)})
    far = far.__class__(**{**far.__dict__, "expiry": T0 + timedelta(days=50)})
    h.platform.instruments.add(near)
    h.platform.instruments.add(far)
    series = random_walk_candles(
        near,
        T0 - timedelta(days=800),
        800,
        interval_seconds=86400,
        start_price=Decimal(3000),
        volatility=0.008,
        drift=0.002,
        seed=8,
    )
    h.series = {near.instrument_id: series}
    assert h.autopilot.resolve("MCX:GOLDM1!").instrument_id == "MCX:GOLDM26JANFUT"
    assert any(o["id"] == "MCX:GOLDM1!" for o in h.autopilot.universe_options())
    h.autopilot.update_config({"universe": ["MCX:GOLDM1!"]}, "tester")
    h.autopilot.run_cycle()
    [m] = h.managed()
    assert m.instrument_id == "MCX:GOLDM26JANFUT" and m.universe_id == "MCX:GOLDM1!"
    advance(h.clock, days=18)  # within 3 days of expiry
    h.autopilot.monitor()
    [rolled] = h.managed()
    assert rolled.instrument_id == "MCX:GOLDM26FEBFUT" and rolled.created_at == m.created_at
    assert h.autopilot.managed[m.deployment_id].rolled_to == rolled.deployment_id
    assert h.kinds()[0] == "ROLL"
    assert h.autopilot.resolve("MCX:GOLDM1!").instrument_id == "MCX:GOLDM26FEBFUT"


def test_live_arming_is_per_broker_and_futures_need_opt_in():
    h = Harness()
    h.platform.trading.register_account(TradingAccount("fyers-1", "Fyers", "FYERS", AccountMode.LIVE, "INR"))
    h.platform.trading.register_account(
        TradingAccount("binance-1", "Binance", "BINANCE", AccountMode.LIVE, "USDT")
    )
    h.autopilot.arm_live("binance-1", Decimal(10_000), "owner")
    h.autopilot.run_cycle()
    [m] = h.managed()
    assert h.autopilot._live_account_for(m) is None  # an NSE stock cannot go to Binance
    h.autopilot.arm_live("fyers-1", Decimal(50_000), "owner")
    assert h.autopilot._live_account_for(m).account_id == "fyers-1"
    future = h.autopilot.managed[m.deployment_id].__class__(
        **{**m.__dict__, "instruments": ["MCX:GOLDM26JANFUT"]}
    )
    h.platform.instruments.add(gold_future())
    assert h.autopilot._live_account_for(future) is None  # futures not allowed on that arming
    h.autopilot.arm_live("fyers-1", Decimal(50_000), "owner", allow_futures=True)
    assert h.autopilot._live_account_for(future).account_id == "fyers-1"
    h.autopilot.disarm_live("owner", account_id="binance-1")
    assert set(h.autopilot.live.accounts) == {"fyers-1"}


def test_book_drawdown_cuts_everything_to_cash_and_pauses():
    h = Harness()
    h.autopilot.update_config({"portfolio_drawdown_limit": 0.02, "max_deployment_drawdown": 0.9}, "tester")
    h.autopilot.run_cycle()
    [m] = h.managed()
    h.paper_trade(m, price="1000", qty="100")
    set_quote(h.platform, TREND, "849", "851")  # -₹15k on a ₹5 lakh book = 3%
    h.autopilot.monitor()
    assert not h.managed() and "HALT" in h.kinds()
    assert h.platform.positions.net_quantity(PAPER_AI_ACCOUNT, TREND) == 0
    h.autopilot.run_cycle()  # still paused: nothing new is deployed
    assert not h.managed() and "paused" in h.autopilot.decisions()[1].title
    advance(h.clock, days=6)
    h.autopilot.run_cycle()
    assert h.managed()


def test_crypto_is_sized_in_its_own_currency():
    from jdquant.autopilot.research import strategy_parameters

    btc = Instrument(
        "BINANCE",
        "BTCUSDT",
        AssetClass.CRYPTO_SPOT,
        "BTC",
        "USDT",
        Decimal("0.01"),
        Decimal("0.00001"),
        Decimal("0.00001"),
    )
    config = ResearchConfig(capital=Decimal(850_000), fx_rates={"INR": Decimal(1), "USDT": Decimal(85)})
    assert config.capital_for(btc) == Decimal(10_000)  # ₹8.5 lakh = 10,000 USDT
    from jdquant.autopilot.research import candidate_grid

    params = strategy_parameters(candidate_grid()[0], config.capital_for(btc), config, btc)
    assert params["bars_per_year"] == 365 and params["allow_short"] is False
    with pytest.raises(PlatformError, match="FX_RATE_MISSING"):
        ResearchConfig(fx_rates={"INR": Decimal(1)}).capital_for(btc)


def test_futures_lots_too_big_for_the_budget_are_skipped_with_a_reason():
    gold = gold_future()
    series = random_walk_candles(
        gold,
        START,
        800,
        interval_seconds=86400,
        start_price=Decimal(72000),
        volatility=0.01,
        drift=0.001,
        seed=2,
    )
    result = research([gold], {gold.instrument_id: series}, ResearchConfig(capital=Decimal(500_000)))
    assert result.evaluations == []
    assert "one lot is worth" in result.skipped[gold.instrument_id]


# ---- the analyst (NVIDIA / OpenAI-compatible) and capital protection --------------------------------


class FakeNim:
    """An OpenAI-compatible chat-completions endpoint answering like a reasoning model."""

    def __init__(self, content: str, status: int = 200):
        self.content, self.status, self.requests = content, status, []

    def client(self):
        import httpx

        def handle(request):
            self.requests.append(request)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": "unavailable"})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": self.content,
                                "reasoning_content": "Thinking about fold returns...",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
                },
            )

        return httpx.Client(transport=httpx.MockTransport(handle))


def _nim(fake, h):
    from jdquant.ai.analyst import openai_compatible_analyst
    from jdquant.ai.prompts import LlmCallLog

    return openai_compatible_analyst(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test-key",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        calls=LlmCallLog(h.platform.store, h.clock),
        http=fake.client(),
    )


def test_nvidia_analyst_request_and_answer():
    import json as _json

    concern = {"instrument_id": "NSE:TREND-EQ", "concern": "profit from one period", "severity": "HIGH"}
    answer = json.dumps({"summary": "The autopilot will paper trade TREND-EQ.", "concerns": [concern]})
    content = "<think>The holdout is positive.</think>Here is my review:\n" + answer
    fake = FakeNim(content)
    h = Harness()
    h.autopilot._analyst = _nim(fake, h)
    run = h.autopilot.run_cycle()
    request = fake.requests[0]
    body = _json.loads(request.content)
    assert request.url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and body["stream"] is False
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["messages"][0]["role"] == "system" and "selected" in _json.loads(
        body["messages"][1]["content"]
    )
    assert run.analyst == "NVIDIA · nvidia/nemotron-3-ultra-550b-a55b"
    assert run.summary == "The autopilot will paper trade TREND-EQ."
    assert run.concerns[0]["severity"] == "high"
    assert h.managed()  # veto is off by default: concerns are advice
    rows = h.platform.store.query("SELECT model, input_tokens FROM llm_calls")
    assert rows[0]["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and rows[0]["input_tokens"] == 1200


def test_analyst_veto_only_removes_trades():
    fake = FakeNim(
        '{"summary": "x", "concerns": [{"instrument_id": "NSE:TREND-EQ", "concern": "one lucky period", '
        '"severity": "high"}]}'
    )
    h = Harness()
    h.autopilot.update_config({"analyst_can_veto": True}, "tester")
    h.autopilot._analyst = _nim(fake, h)
    h.autopilot.run_cycle()
    assert not h.managed()
    assert any(d.title.startswith("Analyst vetoed") for d in h.autopilot.decisions())


def test_analyst_failure_never_blocks_the_cycle():
    h = Harness()
    h.autopilot._analyst = _nim(FakeNim("", status=503), h)
    run = h.autopilot.run_cycle()
    assert run.error is None and run.summary is None and h.managed()


def test_analyst_selection_from_environment(monkeypatch):
    from jdquant.ai.analyst import analyst_from_env

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("JDQ_ANALYST", "none")
    assert analyst_from_env(object()) is None
    monkeypatch.setenv("JDQ_ANALYST", "nvidia")
    assert analyst_from_env(object()) is None  # no key: off, with a warning
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("JDQ_ANALYST", "auto")
    assert analyst_from_env(object()) is not None


def test_loss_floor_scales_down_then_stops_and_locks_in_gains():
    h = Harness()
    h.autopilot.update_config(
        {
            "loss_floor": 0.05,
            "lock_in_gains": 0.5,
            "portfolio_drawdown_limit": 0.9,
            "max_deployment_drawdown": 0.9,
        },
        "tester",
    )
    p = h.autopilot.protection("PAPER")
    assert p["floor"] == Decimal(475_000) and p["exposure"] == 1
    h.autopilot.run_cycle()
    [m] = h.managed()
    h.paper_trade(m, price="1000", qty="100")
    set_quote(h.platform, TREND, "1199", "1201")  # +₹20k
    h.autopilot.monitor()
    gain = h.autopilot.managed[m.deployment_id].pnl  # about ₹20k after charges
    locked = h.autopilot.protection("PAPER")["floor"]
    assert locked == (Decimal(500_000) + gain / 2).quantize(Decimal("0.01"))  # half the gain locked in
    set_quote(h.platform, TREND, "1119", "1121")  # gives back ₹8k: a cushion of about ₹2k of the ₹25k room
    h.autopilot.monitor()
    assert Decimal("0.02") < h.autopilot.protection("PAPER")["exposure"] < Decimal("0.15")
    set_quote(h.platform, TREND, "1099", "1101")  # below the locked-in floor
    h.autopilot.monitor()
    assert not h.managed() and h.autopilot.floor_hit["PAPER"]
    assert "loss floor" in h.autopilot.decisions()[0].title
    h.autopilot.run_cycle()
    assert not h.managed()  # stays in cash until the owner resets
    h.autopilot.reset_protection("PAPER", "owner")
    assert h.autopilot.protection("PAPER")["exposure"] == 1
    h.autopilot.run_cycle()
    assert h.managed()


def test_expensive_strategies_are_rejected_for_charges():
    config = ResearchConfig(capital=Decimal(500_000), slippage_bps=Decimal(80), max_cost_share=0.3)
    result = research([nse("TREND-EQ")], {TREND: trending()}, config)
    busy = [e for e in result.evaluations if e.validation["trades"] >= 20]
    assert busy and all(e.validation["charges"] > 0 for e in busy)
    assert any("charges would eat" in r for e in result.evaluations for r in e.reasons)


def test_daily_loss_limits_on_autopilot_accounts():
    h = Harness()
    h.autopilot.update_config({"daily_loss_limit": 0.02}, "tester")
    profiles = {p.name: p for p in h.platform.risk.profiles}
    limit = profiles["autopilot:paper-ai"].limits[0]
    assert limit.limit_type.value == "MAX_DAILY_LOSS" and limit.threshold == Decimal(10_000)
    assert "workspace-default" in profiles  # the owner's own profiles are untouched
    h.platform.trading.register_account(TradingAccount("fyers-1", "Fyers", "FYERS", AccountMode.LIVE, "INR"))
    h.autopilot.arm_live("fyers-1", Decimal(50_000), "owner")
    assert {p.name for p in h.platform.risk.profiles} >= {"autopilot:paper-ai", "autopilot:fyers-1"}
    h.autopilot.disarm_live("owner")
    assert "autopilot:fyers-1" not in {p.name for p in h.platform.risk.profiles}
