"""The AI research desk: every role runs in order on the platform's data, and ratings are scored forward."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from conftest import BTC

from jdquant.ai.analyst import ChatModel


class DeskChat(ChatModel):
    provider, model = "Fake", "desk-1"

    def __init__(self, rating="BUY"):
        self.rating, self.calls = rating, []

    def ask(self, template, payload, **kw):
        self.calls.append((template.key, payload))
        answers = {
            "desk.analyst": {
                "summary": f"{payload.get('role')} view",
                "bullish_points": ["x"],
                "bearish_points": [],
                "signal": "bullish",
                "confidence": 0.6,
                "data_gaps": [],
            },
            "desk.researcher": {
                "argument": f"{payload.get('side')} case",
                "key_points": [],
                "concessions": [],
            },
            "desk.manager": {
                "stance": "bullish",
                "conviction": 0.55,
                "rationale": "trend",
                "decisive_evidence": [],
            },
            "desk.trader": {
                "action": "BUY",
                "entry": 1,
                "stop": 0.9,
                "target": 1.2,
                "size_pct": 5,
                "holding_period": "5 bars",
                "reasoning": "r",
            },
            "desk.risk": {
                "aggressive": "a",
                "neutral": "n",
                "conservative": "c",
                "main_risks": ["gap"],
                "suggested_changes": [],
            },
            "desk.portfolio_manager": {
                "rating": self.rating,
                "confidence": 0.5,
                "summary": "Modest long.",
                "plan": "p",
                "key_risks": [],
                "what_would_change_the_view": [],
            },
        }
        return "Here you go: " + json.dumps(answers[template.key])


def test_desk_runs_every_role_and_scores_forward(app_ctx):
    client, c, platform, clock = app_ctx
    chat = DeskChat()
    c.services["lab"].chat = chat
    desk = c.services["desk"]
    report = desk.start(BTC, user="u1", interval_seconds=3600, horizon=3, debate_rounds=2, wait=True)
    assert report["status"] == "DONE", report["error"]
    roles = [s["role"] for s in report["steps"]]
    assert roles[:3] == ["technical analyst", "market_structure analyst", "news analyst"]
    assert roles[3:] == [
        "bull researcher",
        "bear researcher",
        "bull researcher",
        "bear researcher",
        "research manager",
        "trader",
        "risk team",
        "portfolio manager",
    ]
    assert report["rating"] == "BUY" and report["summary"] == "Modest long."
    technical = chat.calls[0][1]["data"]
    assert technical["last_close"] > 0 and technical["rsi14"] is not None  # computed, not left to the model
    second_bull = [p for k, p in chat.calls if k == "desk.researcher"][2]
    assert [t["side"] for t in second_bull["debate_so_far"]] == ["bull", "bear"]

    listing = client.get("/api/v1/research-desk/reports").json()
    assert listing["reports"][0]["rating"] == "BUY" and "steps" not in listing["reports"][0]
    assert client.get(f"/api/v1/research-desk/reports/{report['report_id']}").json()["steps"]

    # ratings on synthetic demo data are never scored; on broker history they are, after the horizon
    clock.set(clock.now() + timedelta(hours=4))
    assert desk.score_due() == 0 and desk.scorecard()["scored"] == 0
    stored = desk.get(report["report_id"])
    stored["history_source"] = "broker history"
    c.store.put("research_report", stored["report_id"], stored)
    target = datetime.fromisoformat(stored["target_time"])
    bar = SimpleNamespace(close_ts=target, close=Decimal(str(stored["last_close"] * 1.02)))
    desk._history = lambda instrument, interval, bars: ([bar], "broker history")
    assert desk.score_due() == 1
    card = desk.scorecard()
    assert card["directional"] == 1 and card["hit_rate"] == 1.0 and "too few" in card["verdict"]


def test_desk_needs_a_model_and_reports_failures(app_ctx):
    client, c, platform, _ = app_ctx
    c.services["lab"].chat = None
    r = client.post("/api/v1/research-desk/reports", json={"instrument_id": BTC})
    assert r.status_code == 422 and r.json()["code"] == "AI_UNAVAILABLE"

    class Silent(DeskChat):
        def ask(self, template, payload, **kw):
            return "no json here"

    c.services["lab"].chat = Silent()
    report = c.services["desk"].start(BTC, user="u1", interval_seconds=3600, wait=True)
    assert report["status"] == "FAILED" and "no usable JSON" in report["error"]
