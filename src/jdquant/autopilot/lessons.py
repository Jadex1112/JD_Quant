"""Learning from losing trades.

Every autopilot trade is tracked from entry to exit with a snapshot of the conditions when it was opened
(what each tested strategy held, the spread, RSI, average true range, time left in the session, and the
reason given). When a trade closes at a loss, the AI writes a post-mortem: was it a normal losing trade
(a sound setup that simply did not work, which every strategy has) or a mistake visible at entry, what
kind of mistake, and the lesson.

Lessons are used twice:
- The AI sees the recent lessons for an instrument, and the rules learned so far, with every decision.
- A mistake that repeats (3 times within 30 days) becomes a hard rule the code enforces on new entries,
  with a threshold taken from the losing trades themselves, e.g. "no entry when the spread is above 40%
  of the typical one-minute range". Rules expire after 30 days without new evidence and can be forgotten
  from the Autopilot page.

Normal losses never create rules: reacting to every loss would fit the rules to noise.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.persistence.codec import decode, encode

OPEN_KIND = "ai_trade_open"
LESSON_KIND = "ai_lesson"
STATE_KIND = "ai_lesson_state"
REPEAT = 3  # occurrences of the same mistake before it becomes a rule
WINDOW = timedelta(days=30)

MISTAKES = {
    "normal_loss": "a normal losing trade: the setup was sound and the market went the other way",
    "weak_consensus": "entered when few of the tested strategies agreed",
    "wide_spread": "entered while the spread was large next to the typical range, so costs ate the move",
    "late_session": "entered too close to the session cutoff or close",
    "chased_move": "chased an overextended move (RSI at an extreme at entry)",
    "against_trend": "traded against the longer-term trend",
    "volatility_spike": "entered into a sudden spike that looked like news",
    "other": "another mistake visible at entry",
}
GUARDED = ("weak_consensus", "wide_spread", "late_session", "chased_move")


@dataclass
class OpenTrade:
    deployment_id: str
    instrument_id: str
    mode: str
    direction: int
    quantity: Decimal
    entry_at: datetime
    entry_price: Decimal
    baseline: Decimal  # realized P&L net of fees on this position record before the trade
    label: str = ""
    currency: str = ""
    entry: dict[str, Any] = field(default_factory=dict)  # conditions when the trade was opened
    exit_reason: str | None = None


@dataclass
class Lesson:
    lesson_id: str
    at: datetime
    instrument_id: str
    deployment_id: str
    mode: str
    direction: int
    entry_at: datetime
    exit_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal  # in the instrument's quote currency, after charges
    pnl_inr: float
    minutes_held: int
    exit_reason: str
    label: str
    entry: dict[str, Any] = field(default_factory=dict)
    exit: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | analysed | failed
    mistake: str = ""
    diagnosis: str = ""
    lesson: str = ""
    model: str = ""
    attempts: int = 0


def conditions(facts: dict[str, Any], direction: int) -> dict[str, Any]:
    """The measurable entry conditions the rules use."""
    indicators = facts.get("indicators_1m") or {}
    spread, atr = facts.get("spread_bps"), indicators.get("atr14_bps")
    strategies = facts.get("strategies") or []
    side = "LONG" if direction > 0 else "SHORT"
    return {
        "spread_to_range": round(spread / atr, 3) if spread is not None and atr else None,
        "minutes_to_cutoff": (facts.get("session") or {}).get("minutes_to_cutoff"),
        "rsi14": indicators.get("rsi14"),
        "support_share": (
            round(sum(1 for s in strategies if s.get("holds") == side) / len(strategies), 3)
            if strategies
            else None
        ),
    }


class LessonBook:
    def __init__(self, store, clock):
        self._store, self._clock = store, clock
        self.open: dict[str, OpenTrade] = {
            _key(t.deployment_id, t.instrument_id): t
            for t in (decode(OpenTrade, d) for d in store.all(OPEN_KIND))
        }
        self.lessons: list[Lesson] = sorted(
            (decode(Lesson, d) for d in store.all(LESSON_KIND)), key=lambda x: x.at
        )[-2000:]
        state = store.get(STATE_KIND, "state") or {}
        self.forgotten: dict[str, datetime] = {
            k: datetime.fromisoformat(v) for k, v in state.get("forgotten", {}).items()
        }

    # ---- trades ---------------------------------------------------------------------------------

    def opened(self, trade: OpenTrade) -> None:
        self.open[_key(trade.deployment_id, trade.instrument_id)] = trade
        self._store.put(OPEN_KIND, _key(trade.deployment_id, trade.instrument_id), encode(trade))

    def get(self, deployment_id: str, instrument_id: str) -> OpenTrade | None:
        return self.open.get(_key(deployment_id, instrument_id))

    def note_exit(self, deployment_id: str, instrument_id: str, reason: str) -> None:
        trade = self.get(deployment_id, instrument_id)
        if trade is not None and trade.exit_reason is None:
            trade.exit_reason = reason
            self._store.put(OPEN_KIND, _key(deployment_id, instrument_id), encode(trade))

    def closed(
        self,
        trade: OpenTrade,
        *,
        exit_price: Decimal,
        pnl: Decimal,
        pnl_inr: float,
        exit_facts: dict[str, Any],
    ) -> Lesson | None:
        """Forget the open trade; a loss becomes a lesson waiting for its post-mortem."""
        key = _key(trade.deployment_id, trade.instrument_id)
        self.open.pop(key, None)
        self._store.delete(OPEN_KIND, key)
        if pnl >= 0:
            return None
        now = self._clock.now()
        lesson = Lesson(
            lesson_id=uuid.uuid4().hex[:12],
            at=now,
            instrument_id=trade.instrument_id,
            deployment_id=trade.deployment_id,
            mode=trade.mode,
            direction=trade.direction,
            entry_at=trade.entry_at,
            exit_at=now,
            entry_price=trade.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_inr=round(pnl_inr, 2),
            minutes_held=int((now - trade.entry_at).total_seconds() // 60),
            exit_reason=trade.exit_reason or "the strategy's own exit, stop or session cutoff",
            label=trade.label,
            entry={**trade.entry, "conditions": conditions(trade.entry, trade.direction)},
            exit=exit_facts,
        )
        self.lessons.append(lesson)
        self._save(lesson)
        return lesson

    # ---- post-mortems -----------------------------------------------------------------------------

    def pending(self, limit: int = 5) -> list[Lesson]:
        return [x for x in self.lessons if x.status == "pending"][:limit]

    def briefing(self, lessons: list[Lesson]) -> dict[str, Any]:
        return {
            "losses": [
                {
                    "id": x.lesson_id,
                    "instrument": x.instrument_id,
                    "side": "LONG" if x.direction > 0 else "SHORT",
                    "strategy": x.label,
                    "entry_price": float(x.entry_price),
                    "exit_price": float(x.exit_price),
                    "loss_pct": round(
                        float((x.exit_price - x.entry_price) / x.entry_price) * x.direction * 100, 3
                    )
                    if x.entry_price
                    else None,
                    "loss_inr": x.pnl_inr,
                    "minutes_held": x.minutes_held,
                    "how_it_closed": x.exit_reason,
                    "at_entry": x.entry,
                    "at_exit": x.exit,
                }
                for x in lessons
            ],
            "earlier_lessons": self.recent(None, 8),
            "mistake_types": MISTAKES,
        }

    def apply(self, text: str | None, lessons: list[Lesson], model: str) -> list[Lesson]:
        """Store the post-mortems; returns the lessons that were analysed."""
        answers = _parse(text) if text is not None else {}
        done = []
        for lesson in lessons:
            answer = answers.get(lesson.lesson_id)
            lesson.attempts += 1
            if answer is None:
                if lesson.attempts >= 3:
                    lesson.status = "failed"
                self._save(lesson)
                continue
            lesson.status, lesson.model = "analysed", model
            lesson.mistake = answer["mistake"]
            lesson.diagnosis, lesson.lesson = answer["diagnosis"], answer["lesson"]
            self._save(lesson)
            done.append(lesson)
        return done

    # ---- using what was learned -------------------------------------------------------------------

    def recent(self, instrument_id: str | None, limit: int = 5) -> list[dict[str, Any]]:
        """Recent analysed lessons (mistakes first) for the model's context."""
        rows = [
            x
            for x in reversed(self.lessons)
            if x.status == "analysed" and (instrument_id is None or x.instrument_id == instrument_id)
        ]
        rows.sort(key=lambda x: x.mistake == "normal_loss")
        return [
            {
                "when": x.at.isoformat(timespec="minutes"),
                "instrument": x.instrument_id,
                "side": "LONG" if x.direction > 0 else "SHORT",
                "loss_inr": x.pnl_inr,
                "mistake": x.mistake,
                "lesson": x.lesson,
            }
            for x in rows[:limit]
        ]

    def guards(self) -> list[dict[str, Any]]:
        """Rules learned from mistakes that repeated at least REPEAT times within WINDOW."""
        now = self._clock.now()
        rules = []
        for mistake in GUARDED:
            since = max(now - WINDOW, self.forgotten.get(mistake, now - WINDOW))
            cases = [
                x for x in self.lessons if x.status == "analysed" and x.mistake == mistake and x.at > since
            ]
            if len(cases) < REPEAT:
                continue
            rule = _rule(
                mistake, [x.entry.get("conditions") or {} for x in cases], [x.direction for x in cases]
            )
            if rule is None:
                continue
            rules.append(
                {
                    "mistake": mistake,
                    "cases": len(cases),
                    "instruments": sorted({x.instrument_id for x in cases}),
                    "loss_inr": round(sum(x.pnl_inr for x in cases), 2),
                    "until": (max(x.at for x in cases) + WINDOW).isoformat(timespec="minutes"),
                    **rule,
                }
            )
        return rules

    def check(self, facts: dict[str, Any], direction: int) -> str | None:
        """The learned rule an entry would break, if any."""
        now = conditions(facts, direction)
        for rule in self.guards():
            value = now.get(rule["field"])
            if value is None:
                continue
            limit = rule["limit_long"] if direction > 0 else rule["limit_short"]
            if limit is None:
                continue
            broken = value >= limit if rule["block_when"] == "at_or_above" else value <= limit
            if rule["mistake"] == "chased_move" and direction < 0:
                broken = value <= limit
            if broken:
                return f"learned rule from {rule['cases']} losing trades: {rule['text']} (now {value})"
        return None

    def forget(self, mistake: str) -> None:
        self.forgotten[mistake] = self._clock.now()
        self._store.put(
            STATE_KIND, "state", {"forgotten": {k: v.isoformat() for k, v in self.forgotten.items()}}
        )

    def _save(self, lesson: Lesson) -> None:
        self._store.put(LESSON_KIND, lesson.lesson_id, encode(lesson))


def _rule(mistake: str, entries: list[dict], directions: list[int]) -> dict[str, Any] | None:
    """The threshold that would have blocked every one of those losing entries."""

    def values(name, side=None):
        return [
            e[name]
            for e, d in zip(entries, directions, strict=True)
            if e.get(name) is not None and (side is None or d == side)
        ]

    if mistake == "wide_spread" and (v := values("spread_to_range")):
        limit = min(v)
        return {
            "field": "spread_to_range",
            "block_when": "at_or_above",
            "limit_long": limit,
            "limit_short": limit,
            "text": f"no entry when the spread is {limit:.0%} or more of the typical one-minute range",
        }
    if mistake == "late_session" and (v := values("minutes_to_cutoff")):
        limit = max(v)
        return {
            "field": "minutes_to_cutoff",
            "block_when": "at_or_below",
            "limit_long": limit,
            "limit_short": limit,
            "text": f"no entry {limit:.0f} minutes or less before the session cutoff",
        }
    if mistake == "weak_consensus" and (v := values("support_share")):
        limit = max(v)
        return {
            "field": "support_share",
            "block_when": "at_or_below",
            "limit_long": limit,
            "limit_short": limit,
            "text": f"no entry unless more than {limit:.0%} of the tested strategies agree",
        }
    if mistake == "chased_move":
        longs, shorts = values("rsi14", 1), values("rsi14", -1)
        long_limit = min(longs) if longs and min(longs) >= 60 else None
        short_limit = max(shorts) if shorts and max(shorts) <= 40 else None
        if long_limit is None and short_limit is None:
            return None
        parts = []
        if long_limit is not None:
            parts.append(f"no long with RSI at or above {long_limit:.0f}")
        if short_limit is not None:
            parts.append(f"no short with RSI at or below {short_limit:.0f}")
        return {
            "field": "rsi14",
            "block_when": "at_or_above",
            "limit_long": long_limit,
            "limit_short": short_limit,
            "text": "; ".join(parts),
        }
    return None


def _parse(text: str) -> dict[str, dict[str, str]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data.get("reviews") or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        mistake = str(item.get("mistake", "")).lower()
        out[str(item["id"])] = {
            "mistake": mistake if mistake in MISTAKES else "other",
            "diagnosis": str(item.get("diagnosis", ""))[:600],
            "lesson": str(item.get("lesson", ""))[:300],
        }
    return out


def _key(deployment_id: str, instrument_id: str) -> str:
    return f"{deployment_id}|{instrument_id}"
