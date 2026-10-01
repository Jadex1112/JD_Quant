"""Learning from losing trades: post-mortems, lessons in every decision, and rules from repeated mistakes."""

from datetime import timedelta
from decimal import Decimal

from conftest import advance, set_quote
from test_autopilot import TREND, Harness
from test_monitor import FakeChat

from jdquant.autopilot.lessons import LessonBook, _rule, conditions
from jdquant.autopilot.monitor import TradeMonitor
from jdquant.marketdata.live import LiveMarket


def _setup(mistake="wide_spread"):
    h = Harness()
    live = LiveMarket()
    h.platform.market.listeners.append(live.on_quote)
    chat = FakeChat(lambda kind, item: ("LONG", 0.9), mistake=mistake)
    monitor = TradeMonitor(h.autopilot, chat, live=live)
    h.autopilot.update_config({"decision_mode": "ai"}, "tester")  # opt in: strategies decide by default
    h.autopilot.run_cycle()
    [m] = h.managed()
    for candle in h.series[TREND][-300:]:
        h.runner.hosted[m.deployment_id].history.append(candle)
    base = h.series[TREND][-1].close
    for k in range(40):  # one-minute bars for spread, RSI and range
        _quote(h, base + (k % 3) * 2)
        advance(h.clock, minutes=1)
    _quote(h, base)
    return h, m, monitor, chat, base


def _quote(h, mid, half=Decimal(1)):
    set_quote(h.platform, TREND, str(mid - half), str(mid + half))


def _round(h, monitor, mid, decide, half=Decimal(1)):
    advance(h.clock, minutes=1)
    _quote(h, mid, half)
    monitor.chat.decide = lambda kind, item: (decide, 0.9)
    return monitor.review_now()


def _losing_trade(h, monitor, base):
    _round(h, monitor, base, "LONG")  # enter
    _round(h, monitor, base - 40, "FLAT")  # price falls; the AI closes
    _round(h, monitor, base, "HOLD")  # the closed trade is settled and reviewed


def test_a_losing_trade_gets_a_post_mortem_and_a_lesson():
    h, m, monitor, chat, base = _setup(mistake="late_session")
    _losing_trade(h, monitor, base)
    [lesson] = monitor.lessons.lessons
    assert lesson.status == "analysed" and lesson.mistake == "late_session" and lesson.pnl < 0
    assert lesson.lesson == "wait" and lesson.pnl_inr < 0 and lesson.direction == 1
    loss = chat.post_mortems[0]["losses"][0]
    assert loss["side"] == "LONG" and loss["loss_pct"] < 0 and "closed by the AI" in loss["how_it_closed"]
    entry = loss["at_entry"]
    assert entry["decision"]["action"] == "LONG" and entry["strategies"]
    assert set(entry["conditions"]) == {"spread_to_range", "minutes_to_cutoff", "rsi14", "support_share"}
    assert entry["conditions"]["support_share"] > 0 and entry["conditions"]["spread_to_range"] > 0
    assert "worst_move_pct" in loss["at_exit"] and loss["at_exit"]["worst_move_pct"] < 0
    kinds = h.kinds()
    assert "LOSS" in kinds and "LESSON" in kinds
    # The lesson is part of the next decision's context.
    _round(h, monitor, base, "HOLD")
    item = chat.payloads[-1]["trades"][0]
    assert item["lessons"][0]["mistake"] == "late_session" and item["lessons"][0]["lesson"] == "wait"


def test_a_winning_trade_teaches_nothing():
    h, m, monitor, chat, base = _setup()
    _round(h, monitor, base, "LONG")
    _round(h, monitor, base + 60, "FLAT")
    _round(h, monitor, base, "HOLD")
    assert monitor.lessons.lessons == [] and chat.post_mortems == []


def test_a_repeated_mistake_becomes_an_enforced_rule():
    h, m, monitor, chat, base = _setup(mistake="wide_spread")
    for _ in range(3):
        _losing_trade(h, monitor, base)
    [rule] = monitor.lessons.guards()
    assert rule["mistake"] == "wide_spread" and rule["cases"] == 3 and rule["instruments"] == [TREND]
    assert "no entry when the spread is" in rule["text"]
    assert "RULE" in h.kinds()
    assert chat.payloads[-1]["learned_rules"] == [rule["text"]]
    [review] = _round(h, monitor, base, "LONG", half=Decimal(15))  # a much wider spread than before
    assert not review.acted and "learned rule from 3 losing trades" in review.reason
    held = h.platform.positions.positions(deployment_id=m.deployment_id)
    assert all(p.quantity == 0 for p in held)


def test_normal_losses_never_become_rules():
    h, m, monitor, _, base = _setup(mistake="normal_loss")
    for _ in range(3):
        _losing_trade(h, monitor, base)
    assert len(monitor.lessons.lessons) == 3 and monitor.lessons.guards() == []


def test_rules_can_be_forgotten_and_expire():
    h, m, monitor, _, base = _setup(mistake="wide_spread")
    for _ in range(3):
        _losing_trade(h, monitor, base)
    monitor.lessons.forget("wide_spread")
    assert monitor.lessons.guards() == []
    reloaded = LessonBook(h.platform.store, h.clock)  # survives a restart, forgotten included
    assert len(reloaded.lessons) == 3 and reloaded.guards() == []
    fresh = LessonBook(h.platform.store, h.clock)
    fresh.forgotten.clear()
    assert fresh.guards()
    h.clock.set(h.clock.now() + timedelta(days=31))
    assert fresh.guards() == []  # no new evidence for 30 days


def test_trades_the_strategies_open_themselves_are_reviewed_too():
    h = Harness()
    live = LiveMarket()
    h.platform.market.listeners.append(live.on_quote)
    h.autopilot.update_config({"decision_mode": "strategies"}, "tester")
    chat = FakeChat(lambda kind, item: ("HOLD", 0.9), mistake="chased_move")
    monitor = TradeMonitor(h.autopilot, chat, live=live)
    h.autopilot.run_cycle()
    [m] = h.managed()
    assert m.strategy == "autopilot"
    h.paper_trade(m, price="1000")
    monitor.review_now()  # the open trade is recorded with its conditions
    assert monitor.lessons.get(m.deployment_id, TREND) is not None
    h.platform.oms.submit(_sell(h, m))  # the strategy's own exit, at a loss
    advance(h.clock, minutes=1)
    set_quote(h.platform, TREND, "949", "951")
    monitor.review_now()
    [lesson] = monitor.lessons.lessons
    assert lesson.status == "analysed" and lesson.mistake == "chased_move" and lesson.label == m.label


def _sell(h, m):
    from jdquant.core.types import Side
    from jdquant.oms.orders import OrderRequest, OrderType

    set_quote(h.platform, TREND, "949", "951")
    return OrderRequest(
        "paper-ai", TREND, Side.SELL, OrderType.MARKET, Decimal(100), deployment_id=m.deployment_id
    )


def test_failed_post_mortems_are_retried_then_given_up():
    h, m, monitor, chat, base = _setup()
    _round(h, monitor, base, "LONG")
    _round(h, monitor, base - 40, "FLAT")
    chat.fail_reviews = True
    for _ in range(3):
        _round(h, monitor, base, "HOLD")
    [lesson] = monitor.lessons.lessons
    assert lesson.status == "failed" and lesson.attempts == 3


def test_rule_thresholds_come_from_the_losing_entries():
    entries = [{"spread_to_range": 0.6}, {"spread_to_range": 0.45}, {"spread_to_range": 0.8}]
    assert _rule("wide_spread", entries, [1, 1, -1])["limit_long"] == 0.45
    rsis = [{"rsi14": 78}, {"rsi14": 71}, {"rsi14": 22}]
    rule = _rule("chased_move", rsis, [1, 1, -1])
    assert rule["limit_long"] == 71 and rule["limit_short"] == 22
    assert _rule("chased_move", [{"rsi14": 50}] * 3, [1, 1, 1]) is None  # RSI 50 is not "overextended"
    facts = {
        "spread_bps": 4.0,
        "indicators_1m": {"atr14_bps": 10.0, "rsi14": 55},
        "session": {"minutes_to_cutoff": 30},
        "strategies": [{"holds": "LONG"}, {"holds": "FLAT"}],
    }
    assert conditions(facts, 1) == {
        "spread_to_range": 0.4,
        "minutes_to_cutoff": 30,
        "rsi14": 55,
        "support_share": 0.5,
    }


def test_lessons_api(platform):
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
    body = client.get("/api/v1/autopilot/lessons").json()
    assert body == {"lessons": [], "rules": [], "open_trades": 0}
    assert client.post("/api/v1/autopilot/lessons:forget", json={"mistake": "wide_spread"}).status_code == 200
    assert client.post("/api/v1/autopilot/lessons:forget", json={"mistake": "nope"}).status_code == 422
