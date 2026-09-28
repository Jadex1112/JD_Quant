"""Market replay: run a recorded session forward and stop at any moment to inspect it.

A replay loads the order books recorded for one instrument over a window (up to a day) and feeds them
through fresh copies of the order-book and order-flow engines, so what you see at 10:42:17 is exactly
what the engines knew then: the book on every recorded source, the walls, delta and large trades, and
the events detected up to that point. Seeking backwards starts the engines again from the beginning.
Events recorded live in that window are shown alongside for comparison.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import NotFoundError, ValidationError
from jdquant.intelligence.events import EventEngine, EventStore
from jdquant.intelligence.orderbook import OrderBookEngine
from jdquant.intelligence.orderflow import OrderFlowEngine
from jdquant.marketdata.book import BookSnapshot
from jdquant.marketdata.hub import MarketDataHub
from jdquant.marketdata.recorder import MarketRecorder, MarketStore

MAX_WINDOW = timedelta(hours=26)
MAX_SESSIONS = 5


class ReplaySession:
    def __init__(
        self, recorder: MarketRecorder, events: EventStore, instrument, start: datetime, end: datetime
    ):
        self.replay_id = f"RP-{uuid.uuid4().hex[:10]}"
        self.instrument = instrument
        self.start, self.end = start, end
        self.books: list[BookSnapshot] = recorder.books_between(instrument.instrument_id, start, end)
        if not self.books:
            raise NotFoundError("NOTHING_RECORDED", "no recorded order books for that instrument and window")
        self.recorded_events = [
            e.to_dict()
            for e in sorted(
                events.search(instrument_id=instrument.instrument_id, since=start, until=end, limit=1000),
                key=lambda e: e.at,
            )
        ]
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.cursor = 0
        self.clock = SimulatedClock(self.books[0].received_ts)
        self.events = EventEngine(self.clock, EventStore(MarketStore(":memory:")))
        self.hub = MarketDataHub(self.clock)
        self.orderbook = OrderBookEngine(
            self.events.emit, lambda iid: self.instrument, other_books=self.hub.books
        )
        self.flow = OrderFlowEngine(self.events.emit, lambda iid: self.instrument)
        self.hub.book_listeners += [self.orderbook.on_book, self.flow.on_book]
        self.hub.tick_listeners.append(self.flow.on_tick)

    def info(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "instrument_id": self.instrument.instrument_id,
            "start": self.books[0].received_ts.isoformat(),
            "end": self.books[-1].received_ts.isoformat(),
            "books": len(self.books),
            "sources": sorted({b.source for b in self.books}),
            "recorded_events": len(self.recorded_events),
            "marks": [
                {"at": e["at"], "kind": e["kind"], "severity": e["severity"]} for e in self.recorded_events
            ],
        }

    def frame(self, at: datetime, levels: int = 20) -> dict[str, Any]:
        from jdquant.intelligence.service import book_dict

        with self._lock:
            if self.cursor > 0 and self.books[self.cursor - 1].received_ts > at:
                self._reset()
            while self.cursor < len(self.books) and self.books[self.cursor].received_ts <= at:
                book = self.books[self.cursor]
                self.clock.set(book.received_ts)
                self.hub.publish(book)
                self.cursor += 1
            iid = self.instrument.instrument_id
            flow = self.flow.snapshot(iid) or {}
            return {
                "replay_id": self.replay_id,
                "at": at.isoformat(),
                "position": self.cursor,
                "total": len(self.books),
                "done": self.cursor >= len(self.books),
                "next_at": self.books[self.cursor].received_ts.isoformat()
                if self.cursor < len(self.books)
                else None,
                "books": {s: book_dict(b, levels) for s, b in self.hub.books(iid).items()},
                "walls": self.orderbook.walls(iid),
                "flow": {k: v for k, v in flow.items() if k != "bars"}
                | {"bars": (flow.get("bars") or [])[-60:]},
                "events": [e.to_dict() for e in self.events.recent(iid, 100)],
                "recorded_events": [e for e in self.recorded_events if e["at"] <= at.isoformat()][-100:],
            }


class ReplayManager:
    def __init__(self, recorder: MarketRecorder, events: EventStore, instrument):
        self._recorder = recorder
        self._events = events
        self._instrument = instrument
        self._sessions: OrderedDict[str, ReplaySession] = OrderedDict()
        self._lock = threading.Lock()

    def create(self, instrument_id: str, start: datetime, end: datetime) -> dict[str, Any]:
        instrument = self._instrument(instrument_id)
        if instrument is None:
            raise NotFoundError("INSTRUMENT_NOT_FOUND", f"unknown instrument {instrument_id}")
        if end <= start or end - start > MAX_WINDOW:
            raise ValidationError(
                "INVALID_WINDOW", [{"field": "end", "message": "after start, at most 26 hours"}]
            )
        session = ReplaySession(self._recorder, self._events, instrument, start, end)
        with self._lock:
            self._sessions[session.replay_id] = session
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        return session.info()

    def get(self, replay_id: str) -> ReplaySession:
        session = self._sessions.get(replay_id)
        if session is None:
            raise NotFoundError("REPLAY_NOT_FOUND", f"unknown or expired replay {replay_id}")
        return session

    def delete(self, replay_id: str) -> None:
        with self._lock:
            self._sessions.pop(replay_id, None)


def replay_backtest(
    session: ReplaySession,
    strategy: str,
    params: dict[str, Any],
    *,
    capital: float = 100_000.0,
    decide_every_seconds: float = 5.0,
) -> dict[str, Any]:
    """Run an order-book strategy over a recorded session.

    Features are rebuilt from the replay's engines at each decision point; orders fill against the
    recorded book at the depth-walked average price (so size and spread cost what they would have), plus
    the market's charges. Recorded books cannot show how the market would have reacted to your own orders.
    """
    from decimal import Decimal

    from jdquant.core.types import Side
    from jdquant.intelligence import features
    from jdquant.intelligence.orderbook import expected_fill
    from jdquant.markets.india import fees_for
    from jdquant.oms.orders import Liquidity
    from jdquant.strategy.base import validate_parameters
    from jdquant.strategy.exits import ExitManager, ExitPlan
    from jdquant.strategy.flow import LiquidityWallBounce, OrderFlowMomentum

    classes = {cls.name: cls for cls in (OrderFlowMomentum, LiquidityWallBounce)}
    cls = classes.get(strategy)
    if cls is None:
        raise ValidationError(
            "STRATEGY_UNSUPPORTED", [{"field": "strategy", "message": f"one of {sorted(classes)}"}]
        )
    p = validate_parameters(cls.parameters, params)
    instrument = session.instrument
    iid = instrument.instrument_id
    fees = fees_for(instrument)
    qty = float(p["quantity"])
    exits = ExitManager()
    session._reset()
    position, entry, entry_fee = 0, 0.0, 0.0
    trades, equity = [], []
    realized = 0.0
    last_decision = None
    for book in session.books:
        session.clock.set(book.received_ts)
        session.hub.publish(book)
        if (
            last_decision is not None
            and (book.received_ts - last_decision).total_seconds() < decide_every_seconds
        ):
            continue
        last_decision = book.received_ts
        f = features.build(
            instrument,
            flow=session.flow,
            orderbook=session.orderbook,
            events=session.events,
            book=book,
            now=book.received_ts,
        )
        if not f:
            continue
        price = f["price"]
        reason = None
        if position:
            reason = exits.check(iid, high=price, low=price, close=price, at=book.received_ts)
        decision = cls.decide(f, position, p)
        wanted = position
        reasons: list[str] = []
        if position and reason:
            wanted, reasons = 0, [reason]
        elif decision is not None and decision.target != position and (decision.target == 0 or position == 0):
            wanted, reasons = decision.target, decision.reasons
        if wanted == position:
            unreal = (
                (price - entry) * position * qty * float(instrument.contract_multiplier) if position else 0.0
            )
            equity.append((book.received_ts.isoformat(), capital + realized + unreal))
            continue
        side = Side.BUY if wanted > position else Side.SELL
        fill = expected_fill(book, side.value, qty)
        if fill is None or fill["average_price"] is None:
            continue
        fill_price = fill["average_price"]
        fee = float(
            fees.fee(instrument, side, Decimal(str(qty)), Decimal(str(round(fill_price, 8))), Liquidity.TAKER)
        )
        if position == 0:
            position, entry, entry_fee = wanted, fill_price, fee
            exits.attach(
                iid,
                ExitPlan(
                    wanted,
                    fill_price,
                    book.received_ts,
                    stop=decision.stop,
                    target=decision.take,
                    trail_pct=float(p["trail_pct"]) or None,
                    time_limit_minutes=p["max_hold_minutes"],
                ),
            )
            opened = {"at": book.received_ts.isoformat(), "reasons": reasons, "price": fill_price}
        else:
            gross = (fill_price - entry) * position * qty * float(instrument.contract_multiplier)
            net = gross - entry_fee - fee
            realized += net
            trades.append(
                {
                    "opened_at": opened["at"],
                    "closed_at": book.received_ts.isoformat(),
                    "direction": "LONG" if position > 0 else "SHORT",
                    "entry": entry,
                    "exit": fill_price,
                    "entry_reasons": opened["reasons"],
                    "exit_reason": reasons[0],
                    "net_pnl": net,
                    "fees": entry_fee + fee,
                }
            )
            position = 0
            exits.clear(iid)
        equity.append((book.received_ts.isoformat(), capital + realized))
    from jdquant.analytics.metrics import trade_statistics

    stats = trade_statistics([t["net_pnl"] for t in trades])
    peak, drawdown = capital, 0.0
    for _, value in equity:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak else 0.0)
    return {
        "strategy": strategy,
        "instrument_id": iid,
        "window": {
            "start": session.books[0].received_ts.isoformat(),
            "end": session.books[-1].received_ts.isoformat(),
        },
        "books": len(session.books),
        "net_pnl": realized,
        "return_pct": realized / capital * 100,
        "max_drawdown": drawdown,
        "open_position_at_end": position,
        "trades": trades,
        "stats": stats,
        "equity": equity[:: max(1, len(equity) // 500)],
        "caveats": [
            "Fills use the recorded book; your own orders would have changed it.",
            "Trades are inferred from volume changes between book updates.",
            "One recorded session is a small sample: a sanity check, not evidence of an edge.",
        ],
    }
