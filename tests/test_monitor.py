"""The AI trade monitor: per-minute reviews of autopilot positions and entries, acting, and scoring."""

import json
from datetime import timedelta
from decimal import Decimal

from conftest import advance, set_quote
from test_autopilot import TREND, FakeNim, Harness

from jdquant.ai.analyst import ChatModel, OpenAICompatibleChat
from jdquant.ai.prompts import TRADE_MONITOR, LlmCallLog
from jdquant.autopilot.monitor import TradeMonitor, parse_verdicts
from jdquant.core.types import Side
from jdquant.marketdata.live import LiveMarket
from jdquant.oms.orders import OrderRequest, OrderSource, OrderType


class FakeChat(ChatModel):
    """Answers every id in the payload with the verdict chosen by `decide(kind, item)`."""

    provider, model = "Fake", "fake-1"

    def __init__(self, decide=None, fail=False):
        self.decide = decide or (lambda kind, item: ("HOLD" if kind == "positions" else "APPROVE", 0.9))
        self.fail = fail
        self.payloads = []

    def ask(self, template, payload, *, max_tokens=16384, timeout=None, thinking=True):
        assert template is TRADE_MONITOR
        self.payloads.append(payload)
        if self.fail:
            return None
        answer = {"positions": [], "entries": []}
        for kind in ("positions", "entries"):
            for item in payload[kind]:
                verdict, confidence = self.decide(kind, item)
                answer[kind].append(
                    {"id": item["id"], "verdict": verdict, "confidence": confidence, "reason": "test"}
                )
        return "<think>checking</think>" + json.dumps(answer)


def _setup(decide=None, mode="advise", fail=False, **config):
    h = Harness()
    h.autopilot.run_cycle()
    [m] = h.managed()
    live = LiveMarket()
    h.platform.market.listeners.append(live.on_quote)
    chat = FakeChat(decide, fail)
    monitor = TradeMonitor(h.autopilot, chat, live=live)
    h.runner.entry_gate = monitor.gate
    h.runner.hosted[m.deployment_id].host.ctx.entry_gate = monitor.gate
    h.autopilot.update_config({"monitor_mode": mode, **config}, "tester")
    return h, m, monitor, chat


def _quotes(h, prices):
    for price in prices:
        set_quote(h.platform, TREND, str(Decimal(price) - 1), str(Decimal(price) + 1))
        advance(h.clock, minutes=1)


def _position(h, m):
    return sum(
        (p.quantity for p in h.platform.positions.positions(deployment_id=m.deployment_id)), Decimal(0)
    )


def test_advise_mode_records_verdicts_with_market_context():
    h, m, monitor, chat = _setup(lambda kind, item: ("EXIT", 0.95))
    _quotes(h, [1000 + k for k in range(40)])
    h.paper_trade(m, price="1040")
    [review] = monitor.review_now()
    item = chat.payloads[0]["positions"][0]
    assert item["side"] == "LONG" and item["quantity"] == 100 and item["strategy"] == m.label
    assert item["spread_bps"] == round(2 / 1040 * 10_000, 2) and len(item["bars_1m"]) == 30
    assert item["indicators_1m"]["rsi14"] > 70 and "atr14_bps" in item["indicators_1m"]
    assert item["session"]["name"] == "NSE equity"
    assert review.verdict == "EXIT" and review.confidence == 0.95 and not review.acted
    assert _position(h, m) == 100  # advice only: the strategy trades as backtested
    assert monitor.status()["positions"][0]["verdict"] == "EXIT"


def test_act_mode_closes_on_a_confident_exit_and_blocks_re_entry():
    h, m, monitor, _ = _setup(lambda kind, item: ("EXIT", 0.9), mode="act")
    h.paper_trade(m)
    [review] = monitor.review_now()
    assert review.acted and _position(h, m) == 0
    order = h.platform.oms.list_orders(deployment_id=m.deployment_id)[-1]
    assert order.reduce_only and order.source is OrderSource.SYSTEM and order.submitter == "ai-monitor"
    assert h.kinds()[0] == "AI_EXIT"
    ctx = h.runner.hosted[m.deployment_id].host.ctx
    assert ctx.buy(TREND, Decimal(10)) is None  # cooldown: the strategy cannot jump straight back in
    advance(h.clock, minutes=61)
    monitor._expire_pending(h.clock.now())
    h.autopilot.update_config({"monitor_entry_gate": False}, "tester")
    assert ctx.buy(TREND, Decimal(10)) is not None


def test_low_confidence_and_reduce_verdicts():
    h, m, monitor, _ = _setup(lambda kind, item: ("EXIT", 0.5), mode="act")
    h.paper_trade(m)
    [review] = monitor.review_now()
    assert not review.acted and _position(h, m) == 100  # below the 0.7 confidence floor
    monitor.chat.decide = lambda kind, item: ("REDUCE", 0.8)
    advance(h.clock, minutes=1)
    [review] = monitor.review_now()
    assert review.acted and _position(h, m) == 50 and h.kinds()[0] == "AI_REDUCE"


def test_entries_wait_for_approval_in_act_mode():
    h, m, monitor, chat = _setup(mode="act")
    set_quote(h.platform, TREND, "999", "1001")
    ctx = h.runner.hosted[m.deployment_id].host.ctx
    assert ctx.buy(TREND, Decimal(20)) is None and monitor.due()
    assert ctx.buy(TREND, Decimal(20)) is None and len(monitor.pending) == 1  # asked once
    [review] = monitor.review_now()
    assert chat.payloads[0]["entries"][0]["side"] == "LONG" and review.verdict == "APPROVE"
    assert review.acted and _position(h, m) == 20 and not monitor.pending


def test_rejected_entry_is_dropped_with_a_cooldown():
    h, m, monitor, _ = _setup(lambda kind, item: ("REJECT", 0.85), mode="act")
    set_quote(h.platform, TREND, "999", "1001")
    ctx = h.runner.hosted[m.deployment_id].host.ctx
    ctx.buy(TREND, Decimal(20))
    [review] = monitor.review_now()
    assert review.verdict == "REJECT" and review.acted and _position(h, m) == 0
    assert h.kinds()[0] == "AI_REJECT"
    assert ctx.buy(TREND, Decimal(20)) is None and not monitor.pending  # cooling down: not even asked


def test_unavailable_model_falls_back_as_configured():
    h, m, monitor, _ = _setup(mode="act", fail=True)
    set_quote(h.platform, TREND, "999", "1001")
    ctx = h.runner.hosted[m.deployment_id].host.ctx
    ctx.buy(TREND, Decimal(20))
    assert monitor.review_now() == [] and _position(h, m) == 20  # fallback "allow": the strategy decides
    assert "did not answer" in monitor.last_error
    h.autopilot.update_config({"monitor_fallback": "block"}, "tester")
    ctx.sell(TREND, Decimal(20))
    advance(h.clock, minutes=1)
    ctx.buy(TREND, Decimal(20))
    monitor.review_now()
    assert _position(h, m) == 0


def test_advise_mode_and_other_deployments_are_never_held():
    h, m, monitor, _ = _setup(mode="advise")
    set_quote(h.platform, TREND, "999", "1001")
    ctx = h.runner.hosted[m.deployment_id].host.ctx
    assert ctx.buy(TREND, Decimal(5)) is not None and not monitor.pending

    class Other:
        deployment_id = "manual-deployment"

    assert monitor.gate(Other(), OrderRequest("paper-main", TREND, Side.BUY, OrderType.MARKET, Decimal(1)))


def test_verdicts_are_scored_against_later_prices():
    h, m, monitor, _ = _setup(lambda kind, item: ("HOLD", 0.9))
    h.paper_trade(m, price="1000")
    monitor.review_now()
    advance(h.clock, minutes=16)
    set_quote(h.platform, TREND, "1009", "1011")  # up 1%: holding the long was right
    monitor.chat.decide = lambda kind, item: ("EXIT", 0.9)
    monitor.review_now()  # scores the first review, records an EXIT at 1010
    advance(h.clock, minutes=16)
    set_quote(h.platform, TREND, "1019", "1021")  # up again: that exit call was wrong
    monitor.review_now()
    card = monitor.scorecard()["15"]
    assert card == {"judged": 2, "right": 1, "accuracy": 0.5, "value_of_actions_inr": 0.0}
    first = monitor.reviews[0]
    assert round(first.moves["15"], 4) == 0.01
    reloaded = TradeMonitor(h.autopilot, monitor.chat)
    assert [r.verdict for r in reloaded.reviews][:2] == ["HOLD", "EXIT"]  # persisted


def test_nothing_to_review_makes_no_call_and_budget_is_enforced():
    h, m, monitor, chat = _setup(monitor_max_calls_per_day=1)
    assert monitor.review_now() == [] and chat.payloads == []
    h.paper_trade(m)
    monitor.review_now()
    advance(h.clock, minutes=1)
    assert monitor.review_now() == [] and len(chat.payloads) == 1
    assert "budget" in monitor.last_error


def test_nvidia_request_for_the_monitor():
    fake = FakeNim(json.dumps({"positions": [], "entries": []}))
    h = Harness()
    chat = OpenAICompatibleChat(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test-key",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        calls=LlmCallLog(h.platform.store, h.clock),
        http=fake.client(),
    )
    assert chat.ask(TRADE_MONITOR, {"positions": []}, max_tokens=8192, thinking=False) is not None
    body = json.loads(fake.requests[0].content)
    assert body["max_tokens"] == 8192 and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0]["content"] == TRADE_MONITOR.text
    rows = h.platform.store.query("SELECT template FROM llm_calls")
    assert rows[0]["template"] == "autopilot.trade_monitor@v1"


def test_parse_verdicts_keeps_only_valid_answers():
    text = json.dumps(
        {
            "positions": [
                {"id": "a", "verdict": "exit", "confidence": 1.4, "reason": "x"},
                {"id": "b", "verdict": "BUY", "confidence": 0.9},  # not a position verdict
            ],
            "entries": [{"id": "c", "verdict": "REJECT", "confidence": "high"}],
        }
    )
    assert parse_verdicts(text) == {
        "a": {"verdict": "EXIT", "confidence": 1.0, "reason": "x"},
        "c": {"verdict": "REJECT", "confidence": 0.0, "reason": ""},
    }
    assert parse_verdicts("no json here") is None


def test_monitor_api(platform):
    from fastapi.testclient import TestClient
    from test_autopilot import OWNER, nse

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings, build_context

    platform.instruments.add(nse("TREND-EQ"))
    context = build_context(Settings(), platform)
    client = TestClient(create_app(context=context))
    client.post("/api/v1/setup", json=OWNER)
    token = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    status = client.get("/api/v1/autopilot/monitor").json()
    assert status["enabled"] is True and status["mode"] == "advise" and status["interval_seconds"] == 60
    assert set(status["scorecard"]) >= {"15", "60", "verdicts", "acted"}
    assert client.post("/api/v1/autopilot/monitor:run").json() == {"reviews": []}
    assert client.put("/api/v1/autopilot/config", json={"monitor_mode": "yolo"}).status_code == 400
    assert context.services["runner"].entry_gate == context.services["monitor"].gate
    assert timedelta(seconds=status["interval_seconds"]) == timedelta(minutes=1)
