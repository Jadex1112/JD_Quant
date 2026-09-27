"""The AI trade monitor: an LLM looks at every autopilot trade once a minute.

Each review sends the model the open positions of the autopilot's strategies and the entries they want
to make, with live prices, the spread, recent one-minute bars and indicators. The model answers HOLD,
REDUCE or EXIT for each position and APPROVE or REJECT for each entry, with a confidence and a reason.

When the autopilot's decision mode is "ai", the model also takes the trades. For each instrument it sees
what every walk-forward-tested strategy on that instrument's panel would hold now (long, short or flat),
with their out-of-sample records, and decides LONG, SHORT, FLAT or HOLD. Code enforces the limits: a
direction no tested strategy holds is refused (and an open position whose direction none holds any more is
closed), size is capped by volatility targeting and capital protection, low-confidence opens are ignored,
and nothing opens outside the session. Stops keep working on every bar.

For strategies that trade on their own (decision mode "strategies"), two review modes:
- advise (default): verdicts are recorded and shown; the strategies trade exactly as backtested.
- act: confident EXIT/REDUCE verdicts close or halve the position, and new entries wait for the
  model's approval (up to a few minutes) before the order is sent. The model can only take risk away:
  it never opens, adds to or reverses a position.

Every verdict is scored afterwards against what the price did 15 and 60 minutes later, so the page shows
whether the AI's calls have actually helped. An LLM's judgement cannot be backtested, so this live record
is the only evidence of its value; switch to "act" only once it is convincing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Any

from jdquant.ai.prompts import LOSS_REVIEW, TRADE_MONITOR
from jdquant.autopilot.lessons import LessonBook, OpenTrade
from jdquant.core.types import Side
from jdquant.markets.sessions import LONDON, NEW_YORK, asset_group, session_for
from jdquant.oms.orders import OrderRequest, OrderSource, OrderType
from jdquant.persistence.codec import decode, encode
from jdquant.strategy.indicators import ema_series, rsi
from jdquant.trading.engine import DeploymentState

if TYPE_CHECKING:
    from jdquant.ai.analyst import ChatModel
    from jdquant.autopilot.engine import Autopilot, Managed
    from jdquant.marketdata.records import Candle

log = logging.getLogger(__name__)

KIND = "monitor_review"
HORIZONS = (15, 60)  # minutes after a verdict at which it is scored
ENTRY_MAX_WAIT = timedelta(minutes=3)  # an entry not reviewed by then is dropped
REJECT_COOLDOWN = timedelta(minutes=15)
FLAT_MOVE = 0.0001  # moves under 1 basis point count as neither right nor wrong


@dataclass
class Review:
    review_id: str
    at: datetime
    kind: str  # POSITION | ENTRY | TRADE
    deployment_id: str
    instrument_id: str
    mode: str  # PAPER | LIVE
    direction: int  # +1 long, -1 short (for entries: the proposed side)
    quantity: Decimal
    price: Decimal
    verdict: str  # HOLD | REDUCE | EXIT | APPROVE | REJECT | LONG | SHORT | FLAT | NO_ANSWER
    confidence: float
    reason: str
    acted: bool = False
    model: str = ""
    label: str = ""
    currency: str = ""
    # "15" / "60" -> the price move in the position's favour (None: missed, e.g. the server was down)
    moves: dict[str, float | None] = field(default_factory=dict)


@dataclass
class PendingEntry:
    key: tuple[str, str]
    request: OrderRequest
    created_at: datetime
    price: Decimal
    mode: str
    label: str
    context: dict[str, Any] = field(default_factory=dict)  # market conditions when it was reviewed


class TradeMonitor:
    def __init__(
        self,
        autopilot: Autopilot,
        chat: ChatModel | None,
        *,
        data_source=None,
        live=None,
    ):
        self._a = autopilot
        self._p = autopilot._p
        self._store = autopilot._store
        self.chat = chat
        self._data_source = data_source
        self._live = live
        self.pending: dict[tuple[str, str], PendingEntry] = {}
        self.cooldown: dict[tuple[str, str], datetime] = {}
        self.reviews: list[Review] = sorted(
            (decode(Review, d) for d in self._store.all(KIND)), key=lambda r: r.at
        )[-5000:]
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.calls: dict[str, int] = {}  # per UTC day
        self._busy = threading.Lock()
        self._wake = threading.Event()
        self._seen_stops: dict[tuple[str, str], datetime] = {}
        self.last_reject: str | None = None
        self.lessons = LessonBook(self._store, self._p.clock)
        self._entry_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        autopilot.trade_monitor = self
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def config(self):
        return self._a.config

    @property
    def available(self) -> bool:
        return self.chat is not None

    # ---- background loop ------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="trade-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            try:
                if self.due():
                    self.review_now()
            except Exception:
                log.exception("trade monitor failed")

    def due(self) -> bool:
        if not self.config.monitor_enabled or not self.available:
            return False
        if self.pending:
            return True  # entries are waiting for a verdict
        if not self._open_positions() and not self._traders():
            return False
        if self.last_run_at is None:
            return True
        return self._p.clock.now() - self.last_run_at >= timedelta(
            seconds=self.config.monitor_interval_seconds
        )

    def next_run_at(self) -> datetime | None:
        if not self.config.monitor_enabled or not self.available:
            return None
        if self.last_run_at is None:
            return self._p.clock.now()
        return self.last_run_at + timedelta(seconds=self.config.monitor_interval_seconds)

    # ---- the entry gate (called by strategies, under the platform lock) -----------------------------

    def gate(self, ctx, request: OrderRequest) -> bool:
        """True sends the entry now; False holds it for review (or drops it during a cooldown)."""
        m = self._a.managed.get(ctx.deployment_id)
        if m is None:
            return True  # not an autopilot deployment
        key = (ctx.deployment_id, request.instrument_id)
        now = self._p.clock.now()
        until = self.cooldown.get(key)
        if until is not None and now < until:
            return False  # the monitor recently closed or rejected this trade
        config = self.config
        if not (config.monitor_enabled and config.monitor_mode == "act" and config.monitor_entry_gate):
            return True
        if not self.available or self._out_of_budget():
            return config.monitor_fallback == "allow"
        if key not in self.pending:
            price = self._p.market.reference_price(request.instrument_id) or Decimal(0)
            self.pending[key] = PendingEntry(key, request, now, price, m.mode, m.label)
            self._wake.set()
        return False

    # ---- a review round ---------------------------------------------------------------------------

    def review_now(self) -> list[Review]:
        """Review every open autopilot position and pending entry once. Returns the new reviews."""
        if not self._busy.acquire(blocking=False):
            return []
        try:
            return self._review_round()
        finally:
            self._busy.release()

    def _review_round(self) -> list[Review]:
        now = self._p.clock.now()
        self.last_run_at = now
        self._score(now)
        self._track_trades(now)
        self._post_mortems()
        with self._p.lock:
            self._expire_pending(now)
            positions = self._open_positions()
            entries = list(self.pending.values())
            opportunities = self._opportunities(now)
        if not positions and not entries and not opportunities:
            return []
        if self.chat is None:
            return []
        if self._out_of_budget():
            self.last_error = "daily call budget used up; reviews resume tomorrow"
            self._fallback(entries)
            return []
        items, context = {}, {}
        payload: dict[str, Any] = {
            "time_utc": now.isoformat(timespec="seconds"),
            "learned_rules": [g["text"] for g in self.lessons.guards()],
            "trades": [],
            "positions": [],
            "entries": [],
        }
        for opportunity in opportunities:
            rid = _short_id(f"T{opportunity['instrument_id']}{now.isoformat()}")
            data = self._market_context(opportunity["instrument_id"])
            opportunity["context"] = data
            items[rid] = ("TRADE", None, opportunity)
            lessons = self.lessons.recent(opportunity["instrument_id"])
            payload["trades"].append({"id": rid, **opportunity["facts"], **data, "lessons": lessons})
        for m, position in positions:
            rid = _short_id(f"P{m.deployment_id}{position.instrument_id}{now.isoformat()}")
            data = self._market_context(position.instrument_id)
            items[rid] = ("POSITION", m, position)
            context[rid] = data
            payload["positions"].append({"id": rid, **self._position_facts(m, position, data), **data})
        for entry in entries:
            rid = _short_id(f"E{entry.key}{now.isoformat()}")
            data = self._market_context(entry.request.instrument_id)
            entry.context = data
            items[rid] = ("ENTRY", self._a.managed.get(entry.key[0]), entry)
            context[rid] = data
            lessons = self.lessons.recent(entry.request.instrument_id)
            payload["entries"].append(
                {"id": rid, **self._entry_facts(entry, data), **data, "lessons": lessons}
            )
        self.calls[now.date().isoformat()] = self.calls.get(now.date().isoformat(), 0) + 1
        text = self.chat.ask(
            TRADE_MONITOR,
            payload,
            max_tokens=8192,
            timeout=max(20.0, self.config.monitor_interval_seconds * 0.9),
            thinking=self.config.monitor_reasoning,
        )
        answers = parse_verdicts(text) if text is not None else None
        if answers is None:
            self.last_error = (
                f"{self.chat.provider} did not answer; entries fall back to '{self.config.monitor_fallback}'"
            )
            self._fallback(entries)
            return []
        self.last_error = None
        reviews = []
        with self._p.lock:
            for rid, (kind, m, subject) in items.items():
                answer = answers.get(rid)
                if kind == "TRADE":
                    review = self._trade(subject, answer, now)
                else:
                    review = self._apply(kind, m, subject, answer, now)
                if review is not None:
                    reviews.append(review)
        return reviews

    # ---- facts for the model --------------------------------------------------------------------

    def _open_positions(self) -> list[tuple[Managed, Any]]:
        """Positions of strategies trading on their own (AI-trader positions are handled as trades)."""
        out = []
        for m in self._a.managed.values():
            if m.status != "ACTIVE" or m.strategy == "ai_trader":
                continue
            for p in self._p.positions.positions(deployment_id=m.deployment_id):
                if p.quantity:
                    out.append((m, p))
        return out

    def _bars(self, instrument_id: str) -> list[Candle]:
        source = self._data_source(instrument_id) if self._data_source else None
        if source is not None:
            try:
                return source.fetch_candles(self._p.instruments.get(instrument_id), 60, 60)
            except Exception as exc:  # fall back to bars built from quotes
                log.info("1-minute bars unavailable for %s: %s", instrument_id, exc)
        if self._live is None:
            return []
        from jdquant.marketdata.records import Candle

        return [
            Candle(
                instrument_id,
                60,
                b["open_ts"],
                b["open_ts"] + timedelta(minutes=1),
                Decimal(str(b["open"])),
                Decimal(str(b["high"])),
                Decimal(str(b["low"])),
                Decimal(str(b["close"])),
                Decimal(0),
            )
            for b in self._live.candles(instrument_id, 60, 60)
        ]

    def _market_context(self, instrument_id: str) -> dict[str, Any]:
        instrument = self._p.instruments.get(instrument_id)
        now = self._p.clock.now()
        quote = self._p.market.quote(instrument_id)
        mid = self._p.market.reference_price(instrument_id)
        bars = self._bars(instrument_id)
        closes = [float(c.close) for c in bars]
        session = session_for(instrument)
        facts: dict[str, Any] = {
            "instrument": instrument_id,
            "market": asset_group(instrument),
            "price": _num(mid),
            "session": {
                "name": session.name,
                "open": session.is_open(now),
                "past_intraday_cutoff": session.past_intraday_cutoff(now),
                "minutes_to_cutoff": minutes_to_cutoff(session, now),
                "london_time": now.astimezone(LONDON).strftime("%a %H:%M"),
                "new_york_time": now.astimezone(NEW_YORK).strftime("%a %H:%M"),
            },
        }
        if quote is not None and mid:
            facts["bid"], facts["ask"] = _num(quote.bid_price), _num(quote.ask_price)
            facts["spread_bps"] = round(float((quote.ask_price - quote.bid_price) / mid) * 10_000, 2)
        if closes:
            facts["bars_1m"] = [[_num(c.open), _num(c.high), _num(c.low), _num(c.close)] for c in bars[-30:]]
            facts["indicators_1m"] = _indicators(bars, closes)
        return facts

    def _position_facts(self, m: Managed, position, data: dict) -> dict[str, Any]:
        price = self._p.market.reference_price(position.instrument_id)
        direction = 1 if position.quantity > 0 else -1
        entry = position.average_entry_price
        held = (self._p.clock.now() - position.opened_at).total_seconds() / 60 if position.opened_at else None
        params = m.parameters
        move = float((price - entry) / entry) * direction if price and entry else None
        previous = self._previous(m.deployment_id, position.instrument_id)
        return {
            "side": "LONG" if direction > 0 else "SHORT",
            "quantity": _num(abs(position.quantity)),
            "entry_price": _num(entry),
            "unrealized_pct": round(move * 100, 3) if move is not None else None,
            "unrealized_pnl": _num(position.unrealized_pnl(price)) if price else None,
            "currency": m.currency,
            "minutes_held": round(held) if held is not None else None,
            "mode": m.mode,
            "strategy": m.label,
            "stop_loss_pct": float(Decimal(str(params.get("stop_loss", 0)))) * 100,
            "trailing_stop_pct": float(Decimal(str(params.get("trailing_stop", 0)))) * 100,
            "previous_verdict": (
                {"verdict": previous.verdict, "reason": previous.reason} if previous else None
            ),
        }

    def _previous(self, deployment_id: str, instrument_id: str) -> Review | None:
        for r in reversed(self.reviews):
            if r.kind == "POSITION" and r.deployment_id == deployment_id and r.instrument_id == instrument_id:
                return r
        return None

    def _entry_facts(self, entry: PendingEntry, data: dict) -> dict[str, Any]:
        return {
            "side": "LONG" if entry.request.side is Side.BUY else "SHORT",
            "quantity": _num(entry.request.quantity),
            "signal_price": _num(entry.price),
            "waiting_seconds": round((self._p.clock.now() - entry.created_at).total_seconds()),
            "mode": entry.mode,
            "strategy": entry.label,
        }

    # ---- learning from losing trades ------------------------------------------------------------

    def _position_record(self, deployment_id: str, instrument_id: str):
        for p in self._p.positions.positions(deployment_id=deployment_id, open_only=False):
            if p.instrument_id == instrument_id:
                return p
        return None

    def _track_trades(self, now: datetime) -> None:
        """Open a record for every new autopilot position; a record whose position closed is settled."""
        rates = self._a.fx_rates()
        closed, opened = [], []
        with self._p.lock:
            for trade in list(self.lessons.open.values()):
                p = self._position_record(trade.deployment_id, trade.instrument_id)
                quantity = p.quantity if p is not None else Decimal(0)
                if quantity and (quantity > 0) == (trade.direction > 0):
                    continue
                net = (p.realized_pnl - p.fees_paid) if p is not None else trade.baseline
                exit_price = next(
                    (
                        f.price
                        for f in reversed(self._p.oms.fills)
                        if f.deployment_id == trade.deployment_id and f.instrument_id == trade.instrument_id
                    ),
                    self._p.market.reference_price(trade.instrument_id) or trade.entry_price,
                )
                closed.append((trade, net - trade.baseline, exit_price))
            for m in self._a.managed.values():
                if m.status not in ("ACTIVE", "CLOSING"):
                    continue
                for p in self._p.positions.positions(deployment_id=m.deployment_id):
                    known = self.lessons.get(m.deployment_id, p.instrument_id)
                    flipped = known is not None and (p.quantity > 0) != (known.direction > 0)
                    if p.quantity and (known is None or flipped):
                        opened.append((m, p))
        for trade, pnl, exit_price in closed:
            facts = self._exit_facts(trade, exit_price)
            rate = float(rates.get(trade.currency, 1))
            lesson = self.lessons.closed(
                trade, exit_price=exit_price, pnl=pnl, pnl_inr=float(pnl) * rate, exit_facts=facts
            )
            if lesson is not None:
                self._a._decide(
                    "LOSS",
                    f"Losing trade on {trade.instrument_id}: ₹{lesson.pnl_inr:,.2f}",
                    [
                        f"{'long' if trade.direction > 0 else 'short'} from {trade.entry_price} "
                        f"to {exit_price}, {lesson.minutes_held} minutes; {lesson.exit_reason}",
                        "the AI will review what went wrong",
                    ],
                    instrument_id=trade.instrument_id,
                    deployment_id=trade.deployment_id,
                )
        for m, p in opened:
            key = (m.deployment_id, p.instrument_id)
            snapshot = self._entry_snapshots.pop(key, None)
            if snapshot is None:  # opened by the strategy itself: record the conditions now
                snapshot = {k: v for k, v in self._market_context(p.instrument_id).items() if k != "bars_1m"}
                snapshot["decision"] = {"strategy": m.label}
            realized = p.realized_pnl - p.fees_paid
            self.lessons.opened(
                OpenTrade(
                    m.deployment_id,
                    p.instrument_id,
                    m.mode,
                    1 if p.quantity > 0 else -1,
                    abs(p.quantity),
                    p.opened_at or now,
                    p.average_entry_price,
                    realized,
                    m.label,
                    m.currency,
                    snapshot,
                )
            )

    def _exit_facts(self, trade: OpenTrade, exit_price: Decimal) -> dict[str, Any]:
        facts = {k: v for k, v in self._market_context(trade.instrument_id).items() if k != "bars_1m"}
        bars = [b for b in self._bars(trade.instrument_id) if b.close_ts > trade.entry_at]
        if bars and trade.entry_price:
            entry = float(trade.entry_price)
            best = (
                max(float(b.high) for b in bars) if trade.direction > 0 else min(float(b.low) for b in bars)
            )
            worst = (
                min(float(b.low) for b in bars) if trade.direction > 0 else max(float(b.high) for b in bars)
            )
            facts["best_move_pct"] = round((best / entry - 1) * 100 * trade.direction, 3)
            facts["worst_move_pct"] = round((worst / entry - 1) * 100 * trade.direction, 3)
            facts["minutes_until_worst"] = next(
                (
                    int((b.close_ts - trade.entry_at).total_seconds() // 60)
                    for b in bars
                    if (float(b.low) if trade.direction > 0 else float(b.high)) == worst
                ),
                None,
            )
        return facts

    def _post_mortems(self) -> None:
        """Ask the model why recent losing trades lost (one call for up to five)."""
        pending = self.lessons.pending()
        if not pending or self.chat is None or self._out_of_budget():
            return
        before = {g["mistake"] for g in self.lessons.guards()}
        today = self._p.clock.now().date().isoformat()
        self.calls[today] = self.calls.get(today, 0) + 1
        text = self.chat.ask(
            LOSS_REVIEW,
            self.lessons.briefing(pending),
            max_tokens=8192,
            thinking=self.config.monitor_reasoning,
        )
        with self._p.lock:
            for lesson in self.lessons.apply(text, pending, f"{self.chat.provider} · {self.chat.model}"):
                self._a._decide(
                    "LESSON",
                    f"Lesson from a losing trade on {lesson.instrument_id}: "
                    + (
                        "a normal loss"
                        if lesson.mistake == "normal_loss"
                        else lesson.mistake.replace("_", " ")
                    ),
                    [lesson.diagnosis, f"Lesson: {lesson.lesson}"],
                    instrument_id=lesson.instrument_id,
                    deployment_id=lesson.deployment_id,
                )
            for rule in self.lessons.guards():
                if rule["mistake"] not in before:
                    self._a._decide(
                        "RULE",
                        f"New rule learned from {rule['cases']} losing trades: {rule['text']}",
                        [
                            f"the same mistake ({rule['mistake'].replace('_', ' ')}) cost "
                            f"₹{-rule['loss_inr']:,.2f} on {', '.join(rule['instruments'])}; "
                            f"the rule applies to new entries until {rule['until']}"
                        ],
                    )

    def lessons_status(self) -> dict[str, Any]:
        return {
            "lessons": [encode(x) for x in reversed(self.lessons.lessons[-50:])],
            "rules": self.lessons.guards(),
            "open_trades": len(self.lessons.open),
        }

    # ---- the AI takes trades ---------------------------------------------------------------------

    def _traders(self) -> dict[str, list[tuple[Managed, Any]]]:
        """Active AI-trader deployments with their running strategy, by instrument (paper first)."""
        hosted = getattr(self._a._runner, "hosted", {}) or {}
        out: dict[str, list[tuple[Managed, Any]]] = {}
        for m in sorted(self._a.managed.values(), key=lambda m: m.mode != "PAPER"):
            if m.status != "ACTIVE" or m.strategy != "ai_trader" or m.deployment_id not in hosted:
                continue
            out.setdefault(m.instrument_id, []).append((m, hosted[m.deployment_id].host.strategy))
        return out

    def _held(self, m: Managed, instrument_id: str) -> Decimal:
        held = self._p.positions.positions(deployment_id=m.deployment_id)
        return sum((p.quantity for p in held if p.instrument_id == instrument_id), Decimal(0))

    def _opportunities(self, now: datetime) -> list[dict[str, Any]]:
        """One decision per instrument the AI trades, with every tested strategy's current view."""
        if not self._a.ai_trading():
            return []
        out = []
        for instrument_id, traders in self._traders().items():
            lead, strategy = traders[0]
            self._note_stops(traders)
            views = strategy.views(instrument_id)
            allowed = sorted({v["view"] for v in views if v["view"] in ("LONG", "SHORT")})
            position = self._p.positions.positions(deployment_id=lead.deployment_id)
            current = next((p for p in position if p.instrument_id == instrument_id and p.quantity), None)
            if current is not None and ("LONG" if current.quantity > 0 else "SHORT") not in allowed:
                # No tested strategy holds this direction any more: the AI may not keep it.
                self._close_all(traders, instrument_id, "no tested strategy holds this direction any more")
                current = None
            if not allowed and current is None:
                continue  # nothing any strategy supports: no decision to make
            facts: dict[str, Any] = {
                "strategies": [
                    {"name": v["label"], "holds": v["view"], **(v.get("stats") or {})} for v in views
                ],
                "allowed_directions": allowed + ["FLAT"],
                "position": self._position_facts(lead, current, {}) if current is not None else None,
            }
            out.append(
                {"instrument_id": instrument_id, "traders": traders, "facts": facts, "allowed": allowed}
            )
        return out

    def _note_stops(self, traders) -> None:
        """A stop-out starts the same cooldown as an AI exit, so the AI cannot jump straight back in."""
        for m, strategy in traders:
            for instrument_id, at in getattr(strategy, "stopped_at", {}).items():
                key = (m.deployment_id, instrument_id)
                if self._seen_stops.get(key) != at:
                    self._seen_stops[key] = at
                    self.lessons.note_exit(
                        m.deployment_id, instrument_id, "stop-loss or session cutoff on the bar"
                    )
                    cooldown = timedelta(minutes=self.config.monitor_cooldown_minutes)
                    self.cooldown[key] = self._p.clock.now() + cooldown

    def _trade(self, opportunity: dict, answer: dict | None, now: datetime) -> Review:
        instrument_id, traders = opportunity["instrument_id"], opportunity["traders"]
        lead = traders[0][0]
        price = self._p.market.reference_price(instrument_id) or Decimal(0)
        held = self._held(lead, instrument_id)
        current = 1 if held > 0 else -1 if held < 0 else 0
        action = answer["verdict"] if answer else "NO_ANSWER"
        wanted = {"LONG": 1, "SHORT": -1, "FLAT": 0}.get(action)
        direction = current if wanted in (None, 0) else wanted
        review = self._record("TRADE", lead, instrument_id, direction, abs(held), price, action, answer, now)
        if answer is None or wanted is None or wanted == current:
            return review  # HOLD, no answer, or already there
        reasons = [f"{answer['reason']} (confidence {answer['confidence']:.0%})"]
        if wanted == 0:
            review.acted = self._close_all(traders, instrument_id, answer["reason"])
            self._save(review)
            return review
        blocked = self._blocked(opportunity, lead, action, answer, now)
        if blocked:
            review.reason = f"{answer['reason']} — not traded: {blocked}"
            self._save(review)
            return review
        sized = []
        for m, strategy in traders:
            quantity = self._size(m, strategy, instrument_id, price, answer.get("size", 1.0))
            if quantity > 0:
                sized.append((m, quantity))
        if not sized:
            instrument = self._p.instruments.get(instrument_id)
            review.reason = (
                f"{answer['reason']} — not traded: the size allowed without leverage is below the "
                f"minimum of {instrument.min_quantity} {instrument.base_asset}; raise the budget "
                "or the per-position share"
            )
            self._save(review)
            return review
        if current:
            self._close_all(traders, instrument_id, f"reversing to {action.lower()}")
        side = Side.BUY if wanted > 0 else Side.SELL
        self.last_reject = None
        for m, quantity in sized:
            review.acted = self._submit(m, instrument_id, side, quantity, reduce_only=False) or review.acted
        review.quantity = sized[0][1]
        if review.acted:
            snapshot = {**opportunity["facts"], **opportunity.get("context", {})}
            snapshot.pop("bars_1m", None)
            snapshot["decision"] = {
                "action": action,
                "confidence": answer["confidence"],
                "reason": answer["reason"],
            }
            for m, _ in sized:
                self._entry_snapshots[(m.deployment_id, instrument_id)] = snapshot
        if not review.acted and self.last_reject:
            review.reason = f"{answer['reason']} — order not accepted: {self.last_reject}"
        self._save(review)
        if review.acted:
            supporters = [v["name"] for v in opportunity["facts"]["strategies"] if v["holds"] == action]
            self._a._decide(
                "AI_TRADE",
                f"AI went {action.lower()} {instrument_id} ({sized[0][1]} on paper)",
                [*reasons, f"supported by: {', '.join(supporters)}"],
                instrument_id=instrument_id,
                deployment_id=lead.deployment_id,
            )
        return review

    def _blocked(self, opportunity, lead: Managed, action: str, answer: dict, now: datetime) -> str | None:
        """Why an opening decision may not be traded (None: it may)."""
        instrument = self._p.instruments.get(opportunity["instrument_id"])
        if not self._p.market.reference_price(instrument.instrument_id):
            return "no live price yet"
        if action not in opportunity["allowed"]:
            return f"no tested strategy currently holds {action.lower()}"
        if action == "SHORT" and not instrument.can_short:
            return "this instrument cannot be sold short"
        if answer["confidence"] < self.config.monitor_min_confidence:
            return f"confidence below {self.config.monitor_min_confidence:.0%}"
        until = self.cooldown.get((lead.deployment_id, instrument.instrument_id))
        if until is not None and now < until:
            return f"cooling down after an exit until {until:%H:%M} UTC"
        session = session_for(instrument)
        if not session.is_open(now):
            return "the market is closed"
        if lead.parameters.get("intraday") and session.past_intraday_cutoff(now):
            return "past the intraday cutoff"
        for mode in {m.mode for m, _ in opportunity["traders"]}:
            if self._a.floor_hit.get(mode) or self._a._halted(mode):
                return f"capital protection has stopped new {mode.lower()} trades"
        facts = {**opportunity["facts"], **opportunity.get("context", {})}
        return self.lessons.check(facts, 1 if action == "LONG" else -1)

    def _size(self, m: Managed, strategy, instrument_id: str, price: Decimal, fraction: float) -> Decimal:
        if not price:
            return Decimal(0)
        instrument = self._p.instruments.get(instrument_id)
        exposure = Decimal(str(self._a.protection(m.mode)["exposure"]))
        fraction = Decimal(str(min(1.0, max(0.25, fraction))))
        quantity = instrument.round_quantity_down(
            strategy.max_quantity(instrument_id, price) * fraction * exposure
        )
        return quantity if quantity >= instrument.min_quantity else Decimal(0)

    def _close_all(self, traders, instrument_id: str, reason: str) -> bool:
        closed = False
        for m, _ in traders:
            held = self._held(m, instrument_id)
            if held:
                self.lessons.note_exit(m.deployment_id, instrument_id, f"closed by the AI: {reason}")
                side = Side.SELL if held > 0 else Side.BUY
                closed = self._submit(m, instrument_id, side, abs(held), reduce_only=True) or closed
        if closed:
            self._a._decide(
                "AI_EXIT",
                f"AI closed {instrument_id}",
                [reason],
                instrument_id=instrument_id,
                deployment_id=traders[0][0].deployment_id,
            )
        return closed

    def _submit(
        self, m: Managed, instrument_id: str, side: Side, quantity: Decimal, *, reduce_only: bool
    ) -> bool:
        deployment = self._p.trading.deployments.get(m.deployment_id)
        if deployment is None or deployment.state is not DeploymentState.RUNNING:
            return False
        try:
            order = self._p.oms.submit(
                OrderRequest(
                    deployment.account_id,
                    instrument_id,
                    side,
                    OrderType.MARKET,
                    quantity,
                    deployment_id=m.deployment_id,
                    reduce_only=reduce_only,
                    source=OrderSource.SYSTEM,
                    submitter="ai-trader",
                    tags={"reason": "ai-trader"},
                )
            )
        except Exception as exc:
            log.exception("AI trader order failed on %s", instrument_id)
            self.last_reject = str(exc)
            return False
        if order.status.value in ("REJECTED", "RISK_REJECTED", "EXPIRED", "CANCELED"):
            self.last_reject = order.reject_code or order.status.value
            return False
        return True

    # ---- acting on verdicts ---------------------------------------------------------------------

    def _apply(self, kind: str, m, subject, answer: dict | None, now: datetime) -> Review | None:
        config = self.config
        confident = answer is not None and answer["confidence"] >= config.monitor_min_confidence
        acting = config.monitor_mode == "act"
        if kind == "POSITION":
            position = subject
            price = self._p.market.reference_price(position.instrument_id) or position.average_entry_price
            verdict = answer["verdict"] if answer else "NO_ANSWER"
            review = self._record(
                kind,
                m,
                position.instrument_id,
                1 if position.quantity > 0 else -1,
                abs(position.quantity),
                price,
                verdict,
                answer,
                now,
            )
            if acting and confident and verdict in ("EXIT", "REDUCE"):
                review.acted = self._reduce(m, position, whole=verdict == "EXIT", reason=answer["reason"])
                self._save(review)
            return review
        entry: PendingEntry = subject
        self.pending.pop(entry.key, None)
        direction = 1 if entry.request.side is Side.BUY else -1
        price = self._p.market.reference_price(entry.request.instrument_id) or entry.price
        verdict = answer["verdict"] if answer else "NO_ANSWER"
        review = self._record(
            kind,
            m,
            entry.request.instrument_id,
            direction,
            entry.request.quantity,
            price,
            verdict,
            answer,
            now,
        )
        if m is None:
            return review
        if verdict == "REJECT" and confident:
            self.cooldown[entry.key] = now + REJECT_COOLDOWN
            review.acted = True
            self._a._decide(
                "AI_REJECT",
                f"AI monitor rejected {m.label} entry on {entry.request.instrument_id}",
                [f"{answer['reason']} (confidence {answer['confidence']:.0%})"],
                instrument_id=entry.request.instrument_id,
                deployment_id=m.deployment_id,
            )
        elif answer is None and config.monitor_fallback == "block":
            pass
        else:
            rule = self.lessons.check(entry.context, direction) if entry.context else None
            if rule:
                review.reason = f"{review.reason} — not sent: {rule}"
            else:
                review.acted = self._send(entry)
                if review.acted:
                    snapshot = {k: v for k, v in entry.context.items() if k != "bars_1m"}
                    snapshot["decision"] = {"strategy": entry.label, "verdict": verdict}
                    self._entry_snapshots[entry.key] = snapshot
        self._save(review)
        return review

    def _send(self, entry: PendingEntry) -> bool:
        deployment = self._p.trading.deployments.get(entry.key[0])
        if deployment is None or deployment.state is not DeploymentState.RUNNING:
            return False
        held = self._p.positions.positions(deployment_id=entry.key[0])
        if any(p.instrument_id == entry.request.instrument_id and p.quantity for p in held):
            return False  # the strategy is no longer flat
        try:
            self._p.oms.submit(entry.request)
        except Exception:
            log.exception("sending approved entry failed")
            return False
        return True

    def _reduce(self, m, position, *, whole: bool, reason: str) -> bool:
        instrument = self._p.instruments.get(position.instrument_id)
        quantity = abs(position.quantity)
        if not whole:
            lots = (quantity / 2 / instrument.lot_size).to_integral_value(rounding=ROUND_FLOOR)
            quantity = lots * instrument.lot_size
            if quantity < instrument.min_quantity:
                return False
        deployment = self._p.trading.deployments.get(m.deployment_id)
        if deployment is None or deployment.state not in (DeploymentState.RUNNING, DeploymentState.PAUSED):
            return False
        side = Side.SELL if position.quantity > 0 else Side.BUY
        self.lessons.note_exit(m.deployment_id, position.instrument_id, f"closed by the AI monitor: {reason}")
        try:
            self._p.oms.submit(
                OrderRequest(
                    deployment.account_id,
                    position.instrument_id,
                    side,
                    OrderType.MARKET,
                    quantity,
                    deployment_id=m.deployment_id,
                    reduce_only=True,
                    source=OrderSource.SYSTEM,
                    submitter="ai-monitor",
                    tags={"reason": "ai-monitor"},
                )
            )
        except Exception:
            log.exception("AI monitor could not reduce %s", position.instrument_id)
            return False
        key = (m.deployment_id, position.instrument_id)
        self.cooldown[key] = self._p.clock.now() + timedelta(minutes=self.config.monitor_cooldown_minutes)
        action = "closed" if whole else "halved"
        self._a._decide(
            "AI_EXIT" if whole else "AI_REDUCE",
            f"AI monitor {action} {m.label} on {position.instrument_id}",
            [reason, f"no new entry on this instrument for {self.config.monitor_cooldown_minutes} minutes"],
            instrument_id=position.instrument_id,
            deployment_id=m.deployment_id,
        )
        return True

    def _fallback(self, entries: list[PendingEntry]) -> None:
        """The model is unavailable: send or drop waiting entries as configured."""
        with self._p.lock:
            for entry in entries:
                self.pending.pop(entry.key, None)
                if self.config.monitor_fallback == "allow":
                    self._send(entry)

    def _expire_pending(self, now: datetime) -> None:
        for key, entry in list(self.pending.items()):
            if now - entry.created_at > ENTRY_MAX_WAIT:
                del self.pending[key]
        for key, until in list(self.cooldown.items()):
            if until <= now:
                del self.cooldown[key]

    def _out_of_budget(self) -> bool:
        today = self._p.clock.now().date().isoformat()
        return self.calls.get(today, 0) >= self.config.monitor_max_calls_per_day

    # ---- records and scoring --------------------------------------------------------------------

    def _record(self, kind, m, instrument_id, direction, quantity, price, verdict, answer, now) -> Review:
        review = Review(
            review_id=uuid.uuid4().hex[:16],
            at=now,
            kind=kind,
            deployment_id=m.deployment_id if m else "",
            instrument_id=instrument_id,
            mode=m.mode if m else "",
            direction=direction,
            quantity=Decimal(quantity),
            price=Decimal(price),
            verdict=verdict,
            confidence=answer["confidence"] if answer else 0.0,
            reason=answer["reason"] if answer else "no answer for this item",
            model=f"{self.chat.provider} · {self.chat.model}" if self.chat else "",
            label=m.label if m else "",
            currency=m.currency if m else "",
        )
        self.reviews.append(review)
        del self.reviews[:-5000]
        self._save(review)
        return review

    def _save(self, review: Review) -> None:
        self._store.put(KIND, review.review_id, encode(review))

    def _score(self, now: datetime) -> None:
        """Fill in what the price did 15 and 60 minutes after each verdict."""
        for review in self.reviews:
            for minutes in HORIZONS:
                key = str(minutes)
                if key in review.moves or now - review.at < timedelta(minutes=minutes):
                    continue
                if now - review.at > timedelta(minutes=minutes * 3):
                    review.moves[key] = None  # missed the window
                    self._save(review)
                    continue
                price = self._p.market.reference_price(review.instrument_id)
                if price is None or not review.price:
                    continue
                review.moves[key] = float((price - review.price) / review.price) * review.direction
                self._save(review)

    def scorecard(self) -> dict[str, Any]:
        """How often the verdicts were right, and what acting on them earned or cost (in INR)."""
        rates = self._a.fx_rates()
        out: dict[str, Any] = {}
        for minutes in HORIZONS:
            key = str(minutes)
            right = wrong = 0
            saved = 0.0
            for r in self.reviews:
                move = r.moves.get(key)
                if move is None or abs(move) < FLAT_MOVE or r.verdict == "NO_ANSWER":
                    continue
                cautious = r.verdict in ("EXIT", "REDUCE", "REJECT", "FLAT")
                if cautious == (move < 0):
                    right += 1
                else:
                    wrong += 1
                if r.acted and cautious:
                    notional = float(r.quantity * r.price) * float(rates.get(r.currency, 1))
                    share = 0.5 if r.verdict == "REDUCE" else 1.0
                    saved += -move * notional * share  # positive: loss avoided; negative: gain missed
            judged = right + wrong
            out[key] = {
                "judged": judged,
                "right": right,
                "accuracy": right / judged if judged else None,
                "value_of_actions_inr": round(saved, 2),
            }
        counts: dict[str, int] = {}
        for r in self.reviews:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        out["verdicts"] = counts
        out["acted"] = sum(1 for r in self.reviews if r.acted)
        # The AI trader's own result: profit or loss of the books it runs, in INR.
        for mode in ("PAPER", "LIVE"):
            books = [m for m in self._a.managed.values() if m.strategy == "ai_trader" and m.mode == mode]
            out[f"ai_book_{mode.lower()}_inr"] = float(sum((m.pnl for m in books), Decimal(0)))
        return out

    def status(self) -> dict[str, Any]:
        latest: dict[str, Review] = {}
        for r in self.reviews:
            if r.kind == "POSITION":
                latest[f"{r.deployment_id}:{r.instrument_id}"] = r
        open_keys = {f"{m.deployment_id}:{p.instrument_id}" for m, p in self._open_positions()}
        today = self._p.clock.now().date().isoformat()
        return {
            "available": self.available,
            "provider": self.chat.provider if self.chat else None,
            "model": self.chat.model if self.chat else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": (n.isoformat() if (n := self.next_run_at()) else None),
            "last_error": self.last_error,
            "calls_today": self.calls.get(today, 0),
            "pending": [
                {
                    "instrument_id": e.request.instrument_id,
                    "side": e.request.side.value,
                    "label": e.label,
                    "since": e.created_at.isoformat(),
                }
                for e in self.pending.values()
            ],
            "positions": [encode(r) for k, r in latest.items() if k in open_keys],
            "trader": self._trader_status(),
            "recent": [encode(r) for r in reversed(self.reviews[-50:])],
            "scorecard": self.scorecard(),
        }

    def _trader_status(self) -> list[dict[str, Any]]:
        latest: dict[str, Review] = {}
        for r in self.reviews:
            if r.kind == "TRADE":
                latest[r.instrument_id] = r
        out = []
        with self._p.lock:
            for instrument_id, traders in self._traders().items():
                lead, strategy = traders[0]
                held = self._held(lead, instrument_id)
                views = strategy.views(instrument_id)
                out.append(
                    {
                        "instrument_id": instrument_id,
                        "deployment_id": lead.deployment_id,
                        "position": str(held),
                        "live_copies": sum(1 for m, _ in traders if m.mode == "LIVE"),
                        "views": [{"label": v["label"], "view": v["view"]} for v in views],
                        "decision": encode(latest[instrument_id]) if instrument_id in latest else None,
                    }
                )
        return out


# ---- helpers ------------------------------------------------------------------------------------


def parse_verdicts(text: str) -> dict[str, dict[str, Any]] | None:
    """{id: {verdict, confidence, reason}} from the model's JSON answer; None when unreadable."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    allowed = {
        "positions": ("HOLD", "REDUCE", "EXIT"),
        "entries": ("APPROVE", "REJECT"),
        "trades": ("LONG", "SHORT", "FLAT", "HOLD"),
    }
    out = {}
    for section, verdicts in allowed.items():
        for item in data.get(section) or []:
            if not isinstance(item, dict) or "id" not in item:
                continue
            verdict = str(item.get("verdict") or item.get("action") or "").upper()
            if verdict not in verdicts:
                continue
            try:
                confidence = min(1.0, max(0.0, float(item.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            out[str(item["id"])] = {
                "verdict": verdict,
                "confidence": confidence,
                "reason": str(item.get("reason", ""))[:400],
            }
            if section == "trades":
                try:
                    out[str(item["id"])]["size"] = min(1.0, max(0.0, float(item.get("size", 1))))
                except (TypeError, ValueError):
                    out[str(item["id"])]["size"] = 1.0
    return out


def _indicators(bars, closes: list[float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    value = rsi([Decimal(str(c)) for c in closes], 14)
    if value is not None:
        out["rsi14"] = round(value, 1)
    for n in (20, 50):
        series = ema_series(closes, n) if len(closes) >= n else None
        if series:
            out[f"ema{n}"] = _round(series[-1])
    if len(bars) >= 15:
        ranges = [
            max(float(b.high), float(p.close)) - min(float(b.low), float(p.close))
            for p, b in zip(bars[-15:-1], bars[-14:], strict=True)
        ]
        atr = sum(ranges) / len(ranges)
        out["atr14"] = _round(atr)
        out["atr14_bps"] = round(atr / closes[-1] * 10_000, 2) if closes[-1] else None
    for minutes in (15, 60):
        if len(closes) > minutes:
            out[f"change_{minutes}m_pct"] = round((closes[-1] / closes[-1 - minutes] - 1) * 100, 3)
    window = bars[-60:]
    out["high_60m"] = _round(max(float(b.high) for b in window))
    out["low_60m"] = _round(min(float(b.low) for b in window))
    return out


def _round(value: float) -> float:
    return float(f"{value:.6g}")


def _num(value) -> float | None:
    return None if value is None else _round(float(value))


def _short_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:8]


def minutes_to_cutoff(session, now: datetime) -> int | None:
    """Minutes until the session's intraday cutoff (None for markets that never close)."""
    if session.always_open or not session.is_open(now):
        return None
    local = now.astimezone(session.tz)
    cutoff = datetime.combine(local.date(), session.intraday_cutoff, session.tz)
    if cutoff <= local:
        cutoff += timedelta(days=1)
    return int((cutoff - local).total_seconds() // 60)
