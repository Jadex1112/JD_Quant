"""Signals with reasons, the journal, execution quality, guards, circuit breakers, flow strategies."""

import random
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import BTC, T0, advance, market, set_quote

from jdquant.core.events import Event
from jdquant.core.types import Side
from jdquant.marketdata.book import BookSnapshot, Level
from jdquant.oms.orders import OrderRequest, OrderSource, OrderStatus, OrderType
from jdquant.platform import PAPER_ACCOUNT_ID
from jdquant.strategy.exits import ExitManager, ExitPlan
from jdquant.trading.engine import AccountMode, KillSwitchScope, TradingAccount


def _deployment(platform, strategy="ma_crossover", account=PAPER_ACCOUNT_ID):
    dep = platform.trading.create_deployment(
        strategy_name=strategy,
        strategy_version="1.0.0",
        account_id=account,
        parameters={},
        instruments=[BTC],
        created_by="alice",
    )
    platform.trading.approve(dep.deployment_id, "bob")
    platform.trading.start(dep.deployment_id)
    return dep


def _strategy_order(dep, side=Side.BUY, qty="0.1", **tags):
    return OrderRequest(
        PAPER_ACCOUNT_ID,
        BTC,
        side,
        OrderType.MARKET,
        Decimal(qty),
        deployment_id=dep.deployment_id,
        source=OrderSource.STRATEGY,
        submitter="deployment",
        tags=tags,
    )


def test_exit_rules():
    exits = ExitManager()
    at = T0
    exits.attach("X", ExitPlan(1, 100.0, at, stop=98.0, target=105.0))
    assert exits.check("X", high=101, low=99, close=100.5, at=at) is None
    assert exits.check("X", high=100, low=97.9, close=98.5, at=at) == "STOP_LOSS"
    exits.attach("X", ExitPlan(1, 100.0, at, target=105.0))
    assert exits.check("X", high=105.2, low=100, close=104, at=at) == "TARGET"
    exits.attach("X", ExitPlan(-1, 100.0, at, trail_pct=2.0))
    exits.check("X", high=100, low=95, close=95.5, at=at)
    assert exits.check("X", high=97.1, low=96, close=97.0, at=at) == "TRAILING_STOP"
    exits.attach("X", ExitPlan(1, 100.0, at, stop=97.0, breakeven_after=2.0))
    exits.check("X", high=102.5, low=100, close=102, at=at)
    assert exits.plan("X").stop == 100.0
    assert exits.check("X", high=101, low=99.9, close=100, at=at) == "BREAK_EVEN_STOP"
    exits.attach("X", ExitPlan(1, 100.0, at, time_limit_minutes=30, volatility_multiple=3, entry_range=1.0))
    assert exits.check("X", high=100.5, low=100, close=100, at=at + timedelta(minutes=31)) == "TIME_EXIT"
    exits.attach("X", ExitPlan(1, 100.0, at, volatility_multiple=3, entry_range=1.0))
    assert exits.check("X", high=103, low=99.5, close=101, at=at) == "VOLATILITY_EXIT"


def test_journal_pairs_fills_into_round_trips(app_ctx):
    _, c, platform, clock = app_ctx
    journal = c.services["journal"]
    buy = platform.oms.submit(market(Side.BUY, "0.2"))
    advance(clock, minutes=5)
    set_quote(platform, BTC, "50499", "50501")
    sell = platform.oms.submit(market(Side.SELL, "0.3"))  # closes 0.2, opens a 0.1 short
    trades = journal.trades()
    assert len(trades) == 1 and trades[0]["direction"] == "LONG" and trades[0]["quantity"] == "0.2"
    move = sell.average_fill_price - buy.average_fill_price
    assert Decimal(trades[0]["gross_pnl"]) == Decimal("0.2") * move
    assert Decimal(trades[0]["net_pnl"]) < Decimal(trades[0]["gross_pnl"])  # charges deducted
    assert trades[0]["holding_minutes"] == 5 and trades[0]["exit_reason"] == "manual"
    assert journal.open_trades()[0]["direction"] == "SHORT"
    patterns = journal.patterns()
    assert patterns["trades"] == 1 and patterns["groups"]["direction"][0]["group"] == "LONG"


def test_signal_log_records_reasons_and_risk_blocks(app_ctx):
    _, c, platform, _ = app_ctx
    dep = _deployment(platform)
    ok = platform.oms.submit(
        _strategy_order(dep, reasons="VWAP_RECLAIM,VOLUME_EXPANSION", confidence="0.72", stop="49000")
    )
    assert ok.status is OrderStatus.FILLED
    sig = c.services["signals"].get(ok.order_id)
    assert sig["status"] == "FILLED" and sig["reason_codes"] == ["VWAP_RECLAIM", "VOLUME_EXPANSION"]
    assert sig["confidence"] == 0.72 and sig["stop_loss"] == "49000" and sig["strategy"] == "ma_crossover"
    big = platform.oms.submit(_strategy_order(dep, qty="50", reasons="BREAKOUT"))
    blocked = c.services["signals"].get(big.order_id)
    assert blocked["status"] == "BLOCKED" and blocked["blocked_reason"] == "RISK_MAX_ORDER_NOTIONAL"
    assert any(chk["limit"] == "MAX_ORDER_NOTIONAL" and not chk["passed"] for chk in blocked["risk_checks"])
    execution = c.services["execution"].get(ok.order_id)
    assert execution["expected_price"] == pytest.approx(50001) and execution["slippage"] == pytest.approx(
        0, abs=1
    )


def test_guards_duplicates_positions_slippage_and_corporate_events(app_ctx):
    _, c, platform, clock = app_ctx
    guards, intel = c.services["guards"], c.services["intelligence"]
    dep = _deployment(platform)
    assert platform.oms.submit(_strategy_order(dep)).status is OrderStatus.FILLED
    dup = platform.oms.submit(_strategy_order(dep))
    assert dup.status is OrderStatus.RISK_REJECTED and dup.reject_code == "DUPLICATE_ORDER"
    guards.configure({"duplicate_window_seconds": 0, "max_open_positions": 1})
    set_quote(platform, "BINANCE:ETHUSDT", "3999", "4001")
    eth = platform.oms.submit(market(Side.BUY, "0.1", "BINANCE:ETHUSDT"))
    assert eth.reject_code == "MAX_OPEN_POSITIONS"
    assert platform.oms.submit(market(Side.SELL, "0.1")).status is OrderStatus.FILLED  # exits always pass
    guards.configure({"max_open_positions": 0})
    thin = BookSnapshot(
        BTC,
        "BINANCE",
        clock.now(),
        clock.now(),
        bids=(Level(49999, 0.01),),
        asks=(Level(50001, 0.01), Level(51000, 0.01)),
    )
    intel.hub.publish(thin)
    wide = platform.oms.submit(market(Side.BUY, "0.02"))
    assert wide.reject_code == "EXPECTED_SLIPPAGE"
    guards.configure({"max_expected_slippage_bps": 0})
    intel.news.add_corporate(BTC, "OTHER", clock.now().date(), "token migration")
    intel.news.configure(block_mode="block")
    assert platform.oms.submit(market(Side.BUY, "0.01")).reject_code == "CORPORATE_EVENT"
    intel.news.configure(block_mode="warn")
    assert platform.oms.submit(market(Side.BUY, "0.01")).status is OrderStatus.FILLED
    assert guards.status()["recent_warnings"]


def test_circuit_breakers(app_ctx):
    _, c, platform, clock = app_ctx
    circuit = c.services["circuit"]
    dep = _deployment(platform)
    for _ in range(5):
        circuit._on_trade({"deployment_id": dep.deployment_id, "net_pnl": "-10"})
    assert platform.trading.deployments[dep.deployment_id].state.value == "PAUSED"
    platform.trading.register_account(TradingAccount("live-1", "Live", "BINANCE", AccountMode.LIVE, "USDT"))

    class Rejected:
        account_id, reject_code, reject_reason = "live-1", "INSUFFICIENT_FUNDS", None

    for _ in range(3):
        circuit._on_order_state(
            Event("order.state.changed", {"order": Rejected(), "to": "REJECTED"}, clock.now(), "oms")
        )
    active = [s for s in platform.trading.kill_switches.values() if s.active]
    assert (
        active
        and active[0].scope is KillSwitchScope.ACCOUNT
        and active[0].reason.startswith("REPEATED_REJECTIONS")
    )
    circuit._on_execution(
        {
            "deployment_id": dep.deployment_id,
            "slippage_bps": 250.0,
            "expected_slippage_bps": 5.0,
            "instrument_id": BTC,
        }
    )
    assert any(t["kind"] == "ABNORMAL_SLIPPAGE" for t in circuit.status()["trips"])


def test_reconciliation_flags_a_shortfall(app_ctx):
    _, c, platform, _ = app_ctx
    circuit = c.services["circuit"]
    platform.trading.register_account(TradingAccount("live-2", "Live", "BINANCE", AccountMode.LIVE, "USDT"))
    platform.positions.get_or_create("live-2", BTC, "manual").quantity = Decimal("0.5")

    class Adapter:
        venue = "FAKE"

        def is_ready(self):
            return True

        def fetch_positions(self):
            return {BTC: Decimal("0.2"), "BINANCE:ETHUSDT": Decimal(3)}

    class Conn:
        connection_id, name, venue, account_id, status, last_error = (
            "c1",
            "Fake",
            "FAKE",
            "live-2",
            "CONNECTED",
            None,
        )

    class Connections:
        connections = {"c1": Conn()}
        adapters = {"c1": Adapter()}

    circuit._connections = Connections()
    result = circuit.reconcile()["live-2"]
    states = {r["instrument_id"]: r["state"] for r in result["rows"]}
    assert states == {BTC: "SHORTFALL", "BINANCE:ETHUSDT": "BROKER_ONLY"}
    assert any(s.active and "POSITION_MISMATCH" in s.reason for s in platform.trading.kill_switches.values())


def test_flow_strategies_on_a_replayed_session(app_ctx):
    _, c, platform, clock = app_ctx
    intel = c.services["intelligence"]
    intel.configure({"watch": ["NSE:RELIANCE"], "recording": {"enabled": True, "acknowledge": True}}, "u")
    intel.simulated._rng = random.Random(3)
    start = clock.now()
    for _ in range(1200):
        advance(clock, seconds=1)
        intel.simulated.tick()
    intel.recorder.flush()
    replay = intel.replay.create("NSE:RELIANCE", start, clock.now())
    from jdquant.intelligence.replay import replay_backtest

    params = {
        "quantity": "10",
        "min_size_vs_typical": "5",
        "min_wall_seconds": 0,
        "max_distance_ticks": 15,
        "stop_ticks": 20,
        "target_ticks": 20,
        "max_hold_minutes": 5,
    }
    result = replay_backtest(intel.replay.get(replay["replay_id"]), "liquidity_wall_bounce", params)
    assert result["books"] >= 1000 and result["stats"]["trade_count"] == len(result["trades"])
    assert result["trades"], "a wall-bounce entry should occur in 20 minutes of simulated walls"
    first = result["trades"][0]
    assert "BID_WALL_SUPPORT" in first["entry_reasons"] or "ASK_WALL_RESISTANCE" in first["entry_reasons"]
    flow = replay_backtest(intel.replay.get(replay["replay_id"]), "order_flow_momentum", {"quantity": "10"})
    assert "caveats" in flow and flow["stats"]["trade_count"] == len(flow["trades"])
    features = intel.features("NSE:RELIANCE")
    assert features["price"] and "bid_wall" in features and features["estimated"] is True


def test_vwap_reclaim_backtests_on_bars(app_ctx):
    client, *_ = app_ctx
    body = {
        "strategy": "vwap_reclaim",
        "instrument_id": BTC,
        "parameters": {"quantity": "0.1", "volume_multiple": "0.5"},
        "data": {"start": "2025-01-01T00:00:00Z", "bars": 600, "start_price": "30000", "seed": 4},
    }
    result = client.post("/api/v1/backtests", json=body)
    assert result.status_code == 201, result.text


def test_automation_api(app_ctx):
    client, c, platform, _ = app_ctx
    platform.oms.submit(market(Side.BUY, "0.1"))
    platform.oms.submit(market(Side.SELL, "0.1"))
    status = client.get("/api/v1/automation/status").json()
    assert status["bot"] in ("IDLE", "RUNNING") and status["accounts"][0]["trades_today"] == 1
    journal = client.get("/api/v1/automation/journal").json()
    trade_id = journal["trades"][0]["trade_id"]
    assert client.get(f"/api/v1/automation/journal/{trade_id}").json()["execution"]["exit"]["average_price"]
    review = client.post(f"/api/v1/automation/journal/{trade_id}/review").json()
    assert review["by"] == "template" and review["summary"]
    assert client.get("/api/v1/automation/execution").json()["orders"]
    estimate = client.post(
        "/api/v1/automation/execution/estimate", json={"instrument_id": BTC, "side": "BUY", "quantity": 1}
    ).json()
    assert estimate["touch"] == 50001
    assert (
        client.put("/api/v1/automation/guards", json={"max_open_positions": 3}).json()["max_open_positions"]
        == 3
    )
    assert (
        client.put("/api/v1/automation/circuit", json={"consecutive_losses": 4}).json()["consecutive_losses"]
        == 4
    )
    assert client.get("/api/v1/automation/portfolio").status_code == 200
    assert client.post("/api/v1/automation/reconcile").status_code == 200
