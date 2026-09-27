"""AI-decided trading: the AI takes trades each minute, only in directions tested strategies support."""

import json
from datetime import timedelta
from decimal import Decimal

from conftest import advance, set_quote
from test_autopilot import TREND, Harness
from test_monitor import FakeChat

from jdquant.autopilot.monitor import TradeMonitor
from jdquant.marketdata.instruments import AssetClass
from jdquant.oms.orders import OrderSource
from jdquant.trading.engine import AccountMode, TradingAccount


def _setup(decide=None, fail=False, history=True):
    h = Harness()
    chat = FakeChat(decide or (lambda kind, item: ("LONG" if kind == "trades" else "HOLD", 0.9)), fail)
    monitor = TradeMonitor(h.autopilot, chat)  # attach before research so the AI trader is deployed
    h.autopilot.run_cycle()
    [m] = h.managed()
    hosted = h.runner.hosted[m.deployment_id]
    if history:  # the bars the runner would have loaded as warm-up
        for candle in h.series[TREND][-300:]:
            hosted.history.append(candle)
    last = h.series[TREND][-1].close
    set_quote(h.platform, TREND, str(last - 1), str(last + 1))
    return h, m, monitor, chat, hosted.host.strategy


def _tick(h, minutes=1):
    """Time passes and a fresh price arrives (the risk checks refuse stale prices)."""
    advance(h.clock, minutes=minutes)
    last = h.series[TREND][-1].close
    set_quote(h.platform, TREND, str(last - 1), str(last + 1))


def _held(h, m):
    held = h.platform.positions.positions(deployment_id=m.deployment_id)
    return sum((p.quantity for p in held), Decimal(0))


def test_ai_mode_deploys_an_ai_trader_with_a_panel_of_tested_strategies():
    h, m, _, _, strategy = _setup()
    assert m.strategy == "ai_trader" and m.candidate == "ai_trader" and m.mode == "PAPER"
    panel = json.loads(m.parameters["panel"])
    assert 1 <= len(panel) <= 5 and all(p["params"]["signal"] != "ml" for p in panel)
    run = h.autopilot.runs(1)[0]
    passed = {row["candidate"] for row in run.leaderboard if row["passed"]}
    assert {p["key"] for p in panel} <= passed
    assert panel[0]["stats"]["validation_sharpe"] > 0
    deploy = next(d for d in h.autopilot.decisions() if d.kind == "DEPLOY")
    assert "following" in deploy.title and len(deploy.reasons) == 1 + len(panel)
    assert _held(h, m) == 0  # the AI trader never opens a position by itself


def test_ai_goes_long_when_tested_strategies_support_it():
    h, m, monitor, chat, strategy = _setup()
    views = strategy.views(TREND)
    assert any(v["view"] == "LONG" for v in views)  # an uptrend: trend strategies hold long
    [review] = monitor.review_now()
    item = chat.payloads[0]["trades"][0]
    assert item["allowed_directions"][-1] == "FLAT" and "LONG" in item["allowed_directions"]
    assert {s["holds"] for s in item["strategies"]} <= {"LONG", "SHORT", "FLAT"}
    assert "validation_sharpe" in item["strategies"][0] and item["position"] is None
    assert review.kind == "TRADE" and review.verdict == "LONG" and review.acted
    held = _held(h, m)
    price = h.platform.market.reference_price(TREND)
    assert 0 < held <= strategy.max_quantity(TREND, price)
    order = h.platform.oms.list_orders(deployment_id=m.deployment_id)[-1]
    assert order.submitter == "ai-trader" and order.source is OrderSource.SYSTEM
    assert h.kinds()[0] == "AI_TRADE"
    _tick(h)
    monitor.chat.decide = lambda kind, item: ("LONG", 0.9)
    monitor.review_now()
    assert _held(h, m) == held  # already long: no pyramiding


def test_unsupported_direction_and_low_confidence_are_refused():
    h, m, monitor, _, _ = _setup(lambda kind, item: ("SHORT", 0.95))
    [review] = monitor.review_now()
    assert not review.acted and "no tested strategy currently holds short" in review.reason
    assert _held(h, m) == 0
    monitor.chat.decide = lambda kind, item: ("LONG", 0.5)
    _tick(h)
    [review] = monitor.review_now()
    assert not review.acted and "confidence below 70%" in review.reason and _held(h, m) == 0


def test_size_follows_the_ai_fraction_within_the_cap():
    h, m, monitor, _, strategy = _setup(lambda kind, item: ("LONG", 0.9))
    monitor.chat = _SizedChat(0.5)
    monitor.review_now()
    price = h.platform.market.reference_price(TREND)
    cap = strategy.max_quantity(TREND, price)
    assert _held(h, m) == (cap * Decimal("0.5")).to_integral_value(rounding="ROUND_FLOOR")


class _SizedChat(FakeChat):
    def __init__(self, size):
        super().__init__(lambda kind, item: ("LONG", 0.9))
        self.size = size

    def ask(self, template, payload, **kw):
        answer = json.loads(super().ask(template, payload, **kw).split("</think>")[1])
        for item in answer["trades"]:
            item["size"] = self.size
        return json.dumps(answer)


def test_flat_closes_and_a_direction_no_strategy_holds_is_closed_automatically():
    h, m, monitor, _, strategy = _setup()
    monitor.review_now()
    assert _held(h, m) > 0
    monitor.chat.decide = lambda kind, item: ("FLAT", 0.6)  # closing needs no minimum confidence
    _tick(h)
    [review] = monitor.review_now()
    assert review.acted and _held(h, m) == 0 and h.kinds()[0] == "AI_EXIT"

    monitor.chat.decide = lambda kind, item: ("LONG", 0.9)
    _tick(h)
    monitor.review_now()
    assert _held(h, m) > 0
    strategy._held[TREND] = {k: "FLAT" for k in strategy._held[TREND]}  # every strategy turns flat
    monitor.chat.fail = True  # even with the model down, the rule applies
    _tick(h)
    monitor.review_now()
    assert _held(h, m) == 0
    assert (
        "no tested strategy holds"
        in next(d for d in h.autopilot.decisions() if d.kind == "AI_EXIT").reasons[0]
    )


def test_stop_loss_on_bars_and_cooldown_before_the_ai_may_re_enter():
    h, m, monitor, _, strategy = _setup()
    monitor.review_now()
    entry = h.platform.market.reference_price(TREND)
    bar = h.series[TREND][-1]
    crash = bar.__class__(
        TREND, bar.interval_seconds, h.clock.now(), h.clock.now() + timedelta(days=1),
        entry, entry, entry * Decimal("0.85"), entry * Decimal("0.85"), bar.volume,
    )  # fmt: skip
    set_quote(h.platform, TREND, str(entry * Decimal("0.85") - 1), str(entry * Decimal("0.85") + 1))
    h.runner._deliver(h.runner.hosted[m.deployment_id], crash)
    assert _held(h, m) == 0  # the 8% stop fired on the bar
    _tick(h)
    [review] = monitor.review_now()
    assert not review.acted and "cooling down" in review.reason


def test_nothing_opens_while_the_market_is_closed_or_protection_has_stopped_trading():
    h, m, monitor, _, _ = _setup()
    h.clock.set(h.clock.now() + timedelta(hours=3))  # 17:30 IST: NSE is closed
    [review] = monitor.review_now()
    assert not review.acted and "market is closed" in review.reason
    h.clock.set(h.clock.now() + timedelta(hours=18))  # next morning, open
    h.autopilot.floor_hit["PAPER"] = True
    [review] = monitor.review_now()
    assert not review.acted and "capital protection" in review.reason


def test_without_an_ai_model_the_strategies_trade_on_their_own():
    h = Harness()
    TradeMonitor(h.autopilot, None)
    h.autopilot.run_cycle()
    [m] = h.managed()
    assert m.strategy == "autopilot" and not h.autopilot.ai_trading()
    h.autopilot.update_config({"decision_mode": "strategies"}, "tester")
    assert h.autopilot.config.decision_mode == "strategies"


def test_live_copies_follow_the_ai_decision_sized_to_their_own_capital():
    h, m, monitor, _, strategy = _setup()
    h.platform.trading.register_account(TradingAccount("fyers-1", "Fyers", "FYERS", AccountMode.LIVE, "INR"))
    h.autopilot.arm_live("fyers-1", Decimal(100_000), "owner")
    live = h.autopilot._start_live(m, Decimal(100_000), h.autopilot.live.accounts["fyers-1"])
    assert live.strategy == "ai_trader" and live.deployment_id in h.runner.hosted
    for candle in h.series[TREND][-300:]:
        h.runner.hosted[live.deployment_id].history.append(candle)
    monitor.review_now()
    paper, real = _held(h, m), _held(h, live)
    assert paper > 0 and 0 < real < paper  # ₹100k live copy vs the paper allocation
    assert h.platform.trading.get_deployment(live.deployment_id).account_id == "fyers-1"


def test_status_shows_each_instrument_with_views_and_the_latest_decision():
    h, m, monitor, _, _ = _setup()
    monitor.review_now()
    [row] = monitor.status()["trader"]
    assert row["instrument_id"] == TREND and Decimal(row["position"]) > 0
    assert row["decision"]["verdict"] == "LONG" and row["views"][0]["view"] in ("LONG", "SHORT", "FLAT")
    card = monitor.scorecard()
    assert "ai_book_paper_inr" in card and h.platform.instruments.get(TREND).asset_class is AssetClass.EQUITY
