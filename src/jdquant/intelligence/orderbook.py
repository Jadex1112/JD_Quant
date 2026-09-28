"""Order-book intelligence: large displayed liquidity ("walls") and what happens to it.

For every instrument and every source separately, each new book is compared with the previous one:

- **Detection.** A level is a wall when its quantity is a large multiple of the typical level in the
  visible book and a meaningful share of its side.
- **Persistence.** How long the wall stays, its peak quantity and order count.
- **Consumption vs withdrawal.** When a wall shrinks, the drop is split into the part explained by
  trading at that price (estimated from the change in day volume and where the last trade printed) and
  the part that was cancelled. When the wall is gone it is classified as consumed (mostly traded),
  withdrawn (mostly cancelled) or partial.
- **Migration and reappearance.** Large liquidity that shows up at a nearby price, or at the same
  price again, shortly after a withdrawal is linked to it.
- **Out of view.** With a 5-level book, a wall can drop out of the visible window when better prices
  arrive in front of it. That is not a cancellation and is never reported as one.
- **Cross-feed confirmation.** Whether the same wall is visible on other brokers' feeds at the same
  time. Independent feeds show the same exchange book; their quantities are never added together.

Trade attribution is an estimate: Indian broker feeds give the last trade and the cumulative volume,
not each exchange trade, so "executed near the level" is the volume traded while the last price sat at
or through the wall's price. Nothing here identifies who placed or cancelled orders.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jdquant.intelligence.common import median, money, quantity, tick_size
from jdquant.intelligence.events import DISCLAIMER, MarketEvent
from jdquant.marketdata.book import BUY, SELL, BookSnapshot, Level, infer_tick
from jdquant.marketdata.instruments import Instrument

BID, ASK = "BID", "ASK"


@dataclass
class WallConfig:
    size_multiple: float = 5.0  # quantity at least this many times the typical visible level
    min_share_of_side: float = 0.15  # and at least this share of the visible quantity on its side
    min_levels: int = 3  # levels needed on the book before "typical" means anything
    min_notional: float = 0.0  # optional floor in the quote currency
    approach_ticks: int = 5  # "price approaching the wall" within this many ticks
    gone_below_share: float = 0.25  # a wall counts as removed once under this share of its peak
    consumed_share: float = 0.8  # removed quantity that traded: at least this -> consumed
    withdrawn_share: float = 0.2  # at most this -> withdrawn
    migrate_seconds: float = 30.0
    migrate_ticks: int = 20
    migrate_min_share: float = 0.3
    reappear_seconds: float = 60.0
    out_of_view_seconds: float = 120.0  # forget a wall this long after it scrolled out of view
    stack_window_seconds: float = 10.0
    stack_multiple: float = 3.0  # pulled/stacked quantity vs the typical level


@dataclass
class Wall:
    wall_id: str
    instrument_id: str
    source: str
    side: str  # BID or ASK
    price: float
    first_seen: datetime
    last_seen: datetime
    initial_quantity: float
    initial_orders: int | None
    peak_quantity: float
    peak_orders: int | None
    current_quantity: float
    current_orders: int | None
    typical_level: float
    price_at_detection: float | None
    executed: float = 0.0
    cancelled: float = 0.0
    refilled: float = 0.0  # quantity added back after trades (hidden or replenishing orders)
    state: str = "ACTIVE"  # ACTIVE, OUT_OF_VIEW, CONSUMED, WITHDRAWN, PARTIAL
    removed_at: datetime | None = None
    price_after: float | None = None
    approached: bool = False
    reduced_reported: bool = False
    confirmed_by: set[str] = field(default_factory=set)
    removal_confirmed_by: set[str] = field(default_factory=set)
    out_of_view_since: datetime | None = None
    event_id: str | None = None

    @property
    def duration(self) -> float:
        return ((self.removed_at or self.last_seen) - self.first_seen).total_seconds()

    @property
    def execution_share(self) -> float | None:
        removed = self.executed + self.cancelled
        return self.executed / removed if removed > 0 else None

    def to_dict(self, instrument: Instrument | None = None) -> dict[str, Any]:
        avg_order = self.current_quantity / self.current_orders if self.current_orders else None
        return {
            "wall_id": self.wall_id,
            "instrument_id": self.instrument_id,
            "source": self.source,
            "side": self.side,
            "price": self.price,
            "price_text": money(instrument, self.price),
            "state": self.state,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "removed_at": self.removed_at.isoformat() if self.removed_at else None,
            "duration_seconds": round(self.duration, 1),
            "initial_quantity": self.initial_quantity,
            "peak_quantity": self.peak_quantity,
            "current_quantity": self.current_quantity,
            "initial_orders": self.initial_orders,
            "peak_orders": self.peak_orders,
            "current_orders": self.current_orders,
            "average_order_size": avg_order,
            "size_vs_typical": round(self.peak_quantity / self.typical_level, 1)
            if self.typical_level
            else None,
            "executed": round(self.executed, 2),
            "cancelled": round(self.cancelled, 2),
            "refilled": round(self.refilled, 2),
            "execution_share": None if self.execution_share is None else round(self.execution_share, 3),
            "price_at_detection": self.price_at_detection,
            "price_after": self.price_after,
            "confirmed_by": sorted(self.confirmed_by),
            "removal_confirmed_by": sorted(self.removal_confirmed_by),
            "event_id": self.event_id,
        }


@dataclass
class _State:
    walls: dict[tuple[str, float], Wall] = field(default_factory=dict)
    history: deque = field(default_factory=lambda: deque(maxlen=200))
    typical: float | None = None
    stack: deque = field(default_factory=deque)  # (time, side, change) near the touch without trades
    seq: int = 0


def _index(levels: tuple[Level, ...]) -> dict[float, Level]:
    return {lv.price: lv for lv in levels}


class OrderBookEngine:
    def __init__(
        self,
        emit: Callable[[MarketEvent], MarketEvent | None],
        instrument: Callable[[str], Instrument | None],
        *,
        other_books: Callable[[str], dict[str, BookSnapshot]] | None = None,
        config: WallConfig | None = None,
    ):
        self._emit = emit
        self._instrument = instrument
        self._other_books = other_books or (lambda instrument_id: {})
        self.config = config or WallConfig()
        self._states: dict[tuple[str, str], _State] = {}
        self._lock = threading.Lock()

    # ---- reading --------------------------------------------------------------------------------------

    def walls(self, instrument_id: str) -> dict[str, Any]:
        instrument = self._instrument(instrument_id)
        with self._lock:
            active, history = [], []
            for (iid, _source), state in self._states.items():
                if iid != instrument_id:
                    continue
                active += [w.to_dict(instrument) for w in state.walls.values()]
                history += [w.to_dict(instrument) for w in state.history]
        active.sort(key=lambda w: (w["side"], -w["price"] if w["side"] == BID else w["price"]))
        history.sort(key=lambda w: w["removed_at"] or "", reverse=True)
        return {"active": active, "history": history[:100]}

    def typical_level(self, instrument_id: str, source: str) -> float | None:
        state = self._states.get((instrument_id, source))
        return state.typical if state else None

    def nearest(self, instrument_id: str, side: str, price: float) -> Wall | None:
        """The closest active wall on a side (BID below/at the price, ASK above/at it), any source."""
        best = None
        with self._lock:
            for (iid, _), state in self._states.items():
                if iid != instrument_id:
                    continue
                for wall in state.walls.values():
                    if wall.side != side or wall.state != "ACTIVE":
                        continue
                    if (side == BID and wall.price > price) or (side == ASK and wall.price < price):
                        continue
                    if best is None or abs(wall.price - price) < abs(best.price - price):
                        best = wall
        return best

    # ---- processing -----------------------------------------------------------------------------------

    def on_book(self, book: BookSnapshot, previous: BookSnapshot | None) -> None:
        with self._lock:
            state = self._states.setdefault((book.instrument_id, book.source), _State())
            self._process(state, book, previous)

    def _process(self, state: _State, book: BookSnapshot, previous: BookSnapshot | None) -> None:
        instrument = self._instrument(book.instrument_id)
        tick = infer_tick(previous, book)
        sizes = [lv.quantity for lv in (*book.bids, *book.asks)]
        if len(sizes) >= self.config.min_levels:
            current = median(sizes)
            state.typical = current if state.typical is None else 0.9 * state.typical + 0.1 * current
        typical = state.typical
        price_now = book.last_price or book.mid
        bids, asks = _index(book.bids), _index(book.asks)

        for key, wall in list(state.walls.items()):
            side_levels = bids if wall.side == BID else asks
            self._update_wall(state, wall, side_levels.get(wall.price), book, tick, price_now, instrument)
            if wall.state not in ("ACTIVE", "OUT_OF_VIEW"):
                del state.walls[key]
                state.history.append(wall)
            elif wall.state == "OUT_OF_VIEW" and wall.out_of_view_since is not None:
                if (
                    book.received_ts - wall.out_of_view_since
                ).total_seconds() > self.config.out_of_view_seconds:
                    del state.walls[key]  # scrolled away long ago: stop tracking, no verdict

        if typical:
            for side, levels in ((BID, book.bids), (ASK, book.asks)):
                side_total = sum(lv.quantity for lv in levels)
                for lv in levels:
                    if (side, lv.price) in state.walls or not self._qualifies(lv, typical, side_total):
                        continue
                    self._detect(state, side, lv, book, typical, price_now, instrument)

        if previous is not None and typical:
            self._stacking(state, book, previous, tick, typical, instrument)

    def _qualifies(self, level: Level, typical: float, side_total: float) -> bool:
        c = self.config
        return (
            level.quantity >= c.size_multiple * typical
            and side_total > 0
            and level.quantity >= c.min_share_of_side * side_total
            and level.quantity * level.price >= c.min_notional
        )

    def _detect(self, state, side, level, book, typical, price_now, instrument) -> None:
        state.seq += 1
        wall = Wall(
            f"W-{book.source}-{book.instrument_id}-{state.seq}",
            book.instrument_id,
            book.source,
            side,
            level.price,
            book.received_ts,
            book.received_ts,
            level.quantity,
            level.orders,
            level.quantity,
            level.orders,
            level.quantity,
            level.orders,
            typical,
            price_now,
        )
        wall.confirmed_by = self._seen_elsewhere(wall, book.source)
        state.walls[(side, level.price)] = wall
        kind, title, extra = "WALL_DETECTED", None, {}
        for old in reversed(state.history):
            if old.side != side or old.state not in ("WITHDRAWN", "PARTIAL") or old.removed_at is None:
                continue
            age = (book.received_ts - old.removed_at).total_seconds()
            ticks_apart = abs(old.price - level.price) / tick_size(instrument)
            big_enough = level.quantity >= self.config.migrate_min_share * old.peak_quantity
            if old.price == level.price and age <= self.config.reappear_seconds and big_enough:
                kind = "WALL_REAPPEARED"
                title = (
                    f"{money(instrument, level.price)} {side.lower()} liquidity returned "
                    f"{age:.0f}s after withdrawal"
                )
                extra = {"previous_wall": old.wall_id, "seconds_since_withdrawal": round(age, 1)}
                break
            if (
                0 < ticks_apart <= self.config.migrate_ticks
                and age <= self.config.migrate_seconds
                and big_enough
            ):
                kind = "WALL_MIGRATED"
                direction = "lower" if level.price < old.price else "higher"
                title = (
                    f"Large {side.lower()} liquidity migrated {direction}: {money(instrument, old.price)}"
                    f" -> {money(instrument, level.price)}"
                )
                extra = {
                    "previous_wall": old.wall_id,
                    "from_price": old.price,
                    "to_price": level.price,
                    "previous_quantity": old.peak_quantity,
                    "seconds_since_withdrawal": round(age, 1),
                }
                break
        if title is None:
            title = (
                f"{money(instrument, level.price)} {side.lower()} wall: {quantity(level.quantity)}"
                + (f" in {level.orders} orders" if level.orders else "")
                + f" ({level.quantity / typical:.0f}x a typical level)"
            )
        event = self._emit(
            MarketEvent(
                book.instrument_id,
                kind,
                book.exchange_ts,
                title,
                {
                    "side": side,
                    "price": level.price,
                    "quantity": level.quantity,
                    "orders": level.orders,
                    "size_vs_typical": round(level.quantity / typical, 1),
                    "typical_level": round(typical, 2),
                    "price_now": price_now,
                    "confirmed_by": sorted(wall.confirmed_by),
                    "wall_id": wall.wall_id,
                    **extra,
                },
                source=book.source,
                key=f"{side}:{level.price}",
            )
        )
        wall.event_id = event.event_id if event is not None else None

    def _update_wall(self, state, wall: Wall, level: Level | None, book, tick, price_now, instrument) -> None:
        c = self.config
        traded_here = 0.0
        if tick is not None:
            hits_bid = tick.side in (SELL, "") and tick.price <= wall.price
            lifts_ask = tick.side in (BUY, "") and tick.price >= wall.price
            if (wall.side == BID and hits_bid) or (wall.side == ASK and lifts_ask):
                traded_here = tick.quantity
        through = price_now is not None and (
            (wall.side == BID and price_now < wall.price) or (wall.side == ASK and price_now > wall.price)
        )
        if level is None:
            if self._out_of_view(wall, book):
                if wall.state != "OUT_OF_VIEW":
                    wall.state, wall.out_of_view_since = "OUT_OF_VIEW", book.received_ts
                return
            executed = min(wall.current_quantity, traded_here)
            wall.executed += executed
            wall.cancelled += wall.current_quantity - executed
            wall.current_quantity = 0.0
            self._finish(wall, book, price_now, through, instrument)
            return
        if wall.state == "OUT_OF_VIEW":
            wall.state, wall.out_of_view_since = "ACTIVE", None
        wall.last_seen = book.received_ts
        # displayed change = added - executed - cancelled. Trades at this price explain part of a drop;
        # the rest was cancelled. More traded than the level lost means it was topped up (refilled).
        drop = wall.current_quantity - level.quantity
        wall.executed += traded_here
        unexplained = drop - traded_here
        if unexplained >= 0:
            wall.cancelled += unexplained
        else:
            wall.refilled += -unexplained
        if level.quantity > wall.peak_quantity:
            wall.peak_quantity, wall.peak_orders = level.quantity, level.orders
        wall.current_quantity, wall.current_orders = level.quantity, level.orders
        if price_now is not None and not wall.approached:
            distance = abs(price_now - wall.price) / tick_size(instrument)
            if distance <= c.approach_ticks:
                wall.approached = True
                self._emit(
                    MarketEvent(
                        wall.instrument_id,
                        "WALL_APPROACHED",
                        book.exchange_ts,
                        f"Price {money(instrument, price_now)} approaching the "
                        f"{money(instrument, wall.price)} {wall.side.lower()} wall "
                        f"({quantity(level.quantity)})",
                        {
                            "side": wall.side,
                            "price": wall.price,
                            "quantity": level.quantity,
                            "price_now": price_now,
                            "distance_ticks": round(distance, 1),
                            "wall_id": wall.wall_id,
                        },
                        source=wall.source,
                        key=f"{wall.side}:{wall.price}",
                    )
                )
        if level.quantity < c.gone_below_share * wall.peak_quantity:
            self._finish(wall, book, price_now, through, instrument)
        elif level.quantity < 0.5 * wall.peak_quantity and not wall.reduced_reported:
            wall.reduced_reported = True
            self._emit(
                MarketEvent(
                    wall.instrument_id,
                    "WALL_REDUCED",
                    book.exchange_ts,
                    f"{money(instrument, wall.price)} {wall.side.lower()} wall down to "
                    f"{quantity(level.quantity)} from {quantity(wall.peak_quantity)}",
                    {
                        "side": wall.side,
                        "price": wall.price,
                        "quantity": level.quantity,
                        "peak_quantity": wall.peak_quantity,
                        "executed": round(wall.executed, 2),
                        "cancelled": round(wall.cancelled, 2),
                        "wall_id": wall.wall_id,
                    },
                    source=wall.source,
                    key=f"{wall.side}:{wall.price}",
                )
            )

    def _out_of_view(self, wall: Wall, book: BookSnapshot) -> bool:
        """True when the wall's price lies beyond the deepest level this (full) book can show."""
        levels = book.bids if wall.side == BID else book.asks
        if len(levels) < book.capacity or not levels:
            return False
        deepest = levels[-1].price
        return wall.price < deepest if wall.side == BID else wall.price > deepest

    def _finish(self, wall: Wall, book, price_now, through: bool, instrument) -> None:
        c = self.config
        wall.removed_at = book.received_ts
        wall.price_after = price_now
        share = wall.execution_share or 0.0
        if share >= c.consumed_share:
            wall.state, kind = "CONSUMED", "WALL_CONSUMED"
            verdict = "traded away"
        elif share <= c.withdrawn_share:
            wall.state, kind = "WITHDRAWN", "WALL_WITHDRAWN"
            verdict = "removed without matching trades"
        else:
            wall.state, kind = "PARTIAL", "WALL_PARTIAL"
            verdict = "partly traded, partly cancelled"
        removed = wall.executed + wall.cancelled
        wall.removal_confirmed_by = self._gone_elsewhere(wall, book.source)
        side_word = wall.side.lower()
        title = (
            f"{money(instrument, wall.price)} {side_word} wall {verdict}: "
            f"{share:.0%} of {quantity(removed)} removed traded"
            if kind != "WALL_WITHDRAWN"
            else f"{money(instrument, wall.price)} {side_word} wall withdrawn: "
            f"{1 - share:.0%} of {quantity(removed)} displayed removed without matching trades"
        )
        classification = {
            "WALL_CONSUMED": "Liquidity consumed by trading",
            "WALL_WITHDRAWN": "Large liquidity withdrawal",
            "WALL_PARTIAL": "Liquidity partly traded and partly withdrawn",
        }[kind]
        confidence = self._confidence(wall, book)
        parent = self._emit(
            MarketEvent(
                wall.instrument_id,
                kind,
                book.exchange_ts,
                title,
                {
                    "side": wall.side,
                    "price": wall.price,
                    "initial_quantity": wall.initial_quantity,
                    "peak_quantity": wall.peak_quantity,
                    "initial_orders": wall.initial_orders,
                    "peak_orders": wall.peak_orders,
                    "duration_seconds": round(wall.duration, 1),
                    "executed_near_level": round(wall.executed, 2),
                    "displayed_removed": round(removed, 2),
                    "cancelled_estimate": round(wall.cancelled, 2),
                    "refilled": round(wall.refilled, 2),
                    "execution_share": round(share, 3),
                    "price_before": wall.price_at_detection,
                    "price_after": price_now,
                    "classification": classification,
                    "confidence": confidence,
                    "confirmed_by": sorted(wall.confirmed_by),
                    "removal_confirmed_by": sorted(wall.removal_confirmed_by),
                    "wall_id": wall.wall_id,
                    "estimated_from": "day volume changes and last-trade prices between book updates",
                    "disclaimer": DISCLAIMER,
                },
                source=wall.source,
                key=f"{wall.side}:{wall.price}",
            )
        )
        if through:
            self._emit(
                MarketEvent(
                    wall.instrument_id,
                    "WALL_BROKEN",
                    book.exchange_ts,
                    f"Price broke {money(instrument, wall.price)} after the {side_word} wall went "
                    f"({money(instrument, wall.price_at_detection)} -> {money(instrument, price_now)})",
                    {
                        "side": wall.side,
                        "price": wall.price,
                        "price_before": wall.price_at_detection,
                        "price_after": price_now,
                        "wall_id": wall.wall_id,
                    },
                    source=wall.source,
                    key=f"{wall.side}:{wall.price}",
                    parents=[parent.event_id] if parent is not None else [],
                )
            )

    def _confidence(self, wall: Wall, book: BookSnapshot) -> str:
        """How sure the classification is: how clear-cut the split and how fast the updates came."""
        share = wall.execution_share or 0.0
        clear = share <= 0.1 or share >= 0.9
        many = len(wall.confirmed_by | wall.removal_confirmed_by) >= 1
        if clear and (many or book.capacity >= 20):
            return "High"
        if clear or many:
            return "Medium"
        return "Low"

    def _seen_elsewhere(self, wall: Wall, source: str) -> set[str]:
        seen = set()
        for other, book in self._other_books(wall.instrument_id).items():
            if other == source:
                continue
            levels = book.bids if wall.side == BID else book.asks
            for lv in levels:
                if lv.price == wall.price and lv.quantity >= 0.5 * wall.current_quantity:
                    seen.add(other)
        return seen

    def _gone_elsewhere(self, wall: Wall, source: str) -> set[str]:
        """Other sources that saw this wall and no longer show it."""
        gone = set()
        for other, book in self._other_books(wall.instrument_id).items():
            if other == source or other not in wall.confirmed_by:
                continue
            levels = book.bids if wall.side == BID else book.asks
            if not any(lv.price == wall.price and lv.quantity >= 0.5 * wall.peak_quantity for lv in levels):
                gone.add(other)
        return gone

    def _stacking(self, state: _State, book, previous, tick, typical: float, instrument) -> None:
        """Quantity added or pulled within three ticks of the touch, net of what traded there."""
        c = self.config
        now = book.received_ts
        traded = tick.quantity if tick is not None else 0.0
        for side, cur_levels, prev_levels in (
            (BID, book.bids, previous.bids),
            (ASK, book.asks, previous.asks),
        ):
            if not cur_levels or not prev_levels:
                continue
            touch = prev_levels[0].price
            band = 3 * tick_size(instrument)

            def near(levels, touch=touch, band=band, side=side):
                return sum(
                    lv.quantity
                    for lv in levels
                    if (side == BID and lv.price >= touch - band)
                    or (side == ASK and lv.price <= touch + band)
                )

            change = near(cur_levels) - near(prev_levels)
            if change < 0:
                change = min(0.0, change + traded)  # quantity that traded was not pulled
            if change:
                state.stack.append((now, side, change))
        while state.stack and (now - state.stack[0][0]).total_seconds() > c.stack_window_seconds:
            state.stack.popleft()
        for side in (BID, ASK):
            net = sum(ch for _, s, ch in state.stack if s == side)
            if abs(net) < c.stack_multiple * typical:
                continue
            kind = "LIQUIDITY_ADDED" if net > 0 else "LIQUIDITY_PULLED"
            verb = "stacked" if net > 0 else "pulled"
            self._emit(
                MarketEvent(
                    book.instrument_id,
                    kind,
                    book.exchange_ts,
                    f"{quantity(abs(net))} {verb} near the best {side.lower()} in "
                    f"{c.stack_window_seconds:.0f}s without matching trades",
                    {
                        "side": side,
                        "quantity": round(abs(net), 2),
                        "typical_level": round(typical, 2),
                        "window_seconds": c.stack_window_seconds,
                    },
                    source=book.source,
                    key=side,
                )
            )
            state.stack = deque(x for x in state.stack if x[1] != side)


def expected_fill(book: BookSnapshot, side: str, quantity_wanted: float) -> dict[str, Any] | None:
    """Walk the visible book: the average price a market order of this size would get, and slippage.

    `side` is the order's side (BUY consumes asks). Returns None without a usable book.
    """
    levels = book.asks if side == BUY else book.bids
    if not levels or quantity_wanted <= 0:
        return None
    remaining, cost, filled, used = quantity_wanted, 0.0, 0.0, 0
    for lv in levels:
        if remaining <= 0:
            break
        take = min(remaining, lv.quantity)
        cost += take * lv.price
        filled += take
        remaining -= take
        used += 1
    touch = levels[0].price
    average = cost / filled if filled else None
    slippage = None if average is None else (average - touch if side == BUY else touch - average)
    return {
        "touch": touch,
        "average_price": average,
        "fillable_in_view": filled,
        "unfilled_in_view": max(0.0, remaining),
        "slippage": slippage,
        "slippage_bps": None if slippage is None or not touch else slippage / touch * 10_000,
        "levels_used": used,
    }
