"""Order flow: who is initiating trades, how hard, and how the book responds.

Built from the trade ticks the hub infers between book snapshots (see `jdquant.marketdata.book`), per
instrument and source:

- **Delta.** Buyer-initiated minus seller-initiated volume per one-minute bar, and cumulative delta for
  the session.
- **Aggressive buying/selling.** One side initiating most of the recent volume.
- **Large trades and clusters.** Trades far above this instrument's typical size, and several of them on
  one side in quick succession.
- **Sweeps.** A buy (sell) that traded through several ask (bid) levels of the previous book.
- **Absorption.** Heavy aggressive volume on one side while the price holds: passive orders absorbing it.
- **Exhaustion.** A new high (low) made on fading aggressive volume.
- **Iceberg-like refills.** Far more traded at the best price than was ever displayed there.
- **Order-flow reversal.** Cumulative delta turning against its recent direction.
- **Imbalance.** Visible bids vs offers, and the exchange's total pending buy vs sell quantity.
- **Volume, volatility and spread spikes.**

Everything is an estimate from broker feeds, labelled as such; nothing identifies a participant.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from jdquant.intelligence.common import mean, median, money, quantity, session_day, tick_size
from jdquant.intelligence.events import MarketEvent
from jdquant.marketdata.book import BUY, SELL, BookSnapshot, Tick
from jdquant.marketdata.instruments import Instrument


@dataclass
class FlowConfig:
    window_seconds: float = 60.0  # aggressive-flow window
    aggressive_ratio: float = 3.0  # one side's volume at least this multiple of the other's
    large_multiple: float = 5.0  # large trade: this many times the median trade
    large_min_samples: int = 30
    cluster_count: int = 3
    cluster_seconds: float = 10.0
    sweep_levels: int = 3
    absorption_seconds: float = 30.0
    iceberg_multiple: float = 2.0
    spike_multiple: float = 3.0
    spike_lookback: int = 20  # bars
    imbalance_threshold: float = 0.6  # |bid - ask| / (bid + ask) of the top five levels


@dataclass
class FlowBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    buy: float = 0.0
    sell: float = 0.0
    trades: int = 0
    max_trade: float = 0.0
    turnover: float = 0.0

    @property
    def delta(self) -> float:
        return self.buy - self.sell

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": int(self.start.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": round(self.volume, 4),
            "buy": round(self.buy, 4),
            "sell": round(self.sell, 4),
            "delta": round(self.delta, 4),
            "trades": self.trades,
            "max_trade": self.max_trade,
            "vwap": self.turnover / self.volume if self.volume else None,
        }


@dataclass
class _State:
    day: date | None = None
    bars: deque = field(default_factory=lambda: deque(maxlen=720))
    cvd: float = 0.0
    day_volume: float = 0.0
    day_turnover: float = 0.0
    day_turnover_sq: float = 0.0
    at_price: dict[float, float] = field(default_factory=dict)  # session volume at each price
    sizes: deque = field(default_factory=lambda: deque(maxlen=500))
    window: deque = field(default_factory=deque)  # (time, side, qty, price)
    large: deque = field(default_factory=lambda: deque(maxlen=50))
    last_book: BookSnapshot | None = None
    prev_book: BookSnapshot | None = None  # the book before `last_book` (the hub sends books before ticks)
    spread_avg: float | None = None
    spread_wide: bool = False
    touch: dict[str, tuple[float, float, float]] = field(
        default_factory=dict
    )  # side -> price, shown, executed
    last_price: float | None = None


class OrderFlowEngine:
    def __init__(
        self,
        emit: Callable[[MarketEvent], MarketEvent | None],
        instrument: Callable[[str], Instrument | None],
        config: FlowConfig | None = None,
    ):
        self._emit = emit
        self._instrument = instrument
        self.config = config or FlowConfig()
        self._states: dict[tuple[str, str], _State] = {}
        self._lock = threading.Lock()

    def _state(self, instrument_id: str, source: str) -> _State:
        return self._states.setdefault((instrument_id, source), _State())

    def sources(self, instrument_id: str) -> list[str]:
        with self._lock:
            return [s for (iid, s) in self._states if iid == instrument_id]

    # ---- book updates ---------------------------------------------------------------------------------

    def on_book(self, book: BookSnapshot, previous: BookSnapshot | None) -> None:
        instrument = self._instrument(book.instrument_id)
        with self._lock:
            state = self._state(book.instrument_id, book.source)
            state.prev_book, state.last_book = state.last_book, book
            spread = book.spread
            tick = tick_size(instrument)
            if spread is not None and spread > 0:
                avg = state.spread_avg
                state.spread_avg = spread if avg is None else 0.98 * avg + 0.02 * spread
                wide = avg is not None and spread >= self.config.spike_multiple * avg and spread >= 2 * tick
                if wide and not state.spread_wide:
                    state.spread_wide = True
                    self._event(
                        book,
                        "SPREAD_WIDENING",
                        f"Spread {money(instrument, spread)} vs usual {money(instrument, avg)}",
                        {"spread": spread, "usual": avg},
                    )
                elif state.spread_wide and avg is not None and spread <= 1.5 * avg:
                    state.spread_wide = False
                    self._event(
                        book,
                        "SPREAD_NORMALIZED",
                        f"Spread back to {money(instrument, spread)}",
                        {"spread": spread, "usual": avg},
                    )
            for side, level in (("BID", book.best_bid), ("ASK", book.best_ask)):
                if level is None:
                    continue
                price, shown, executed = state.touch.get(side, (None, 0.0, 0.0))
                if price == level.price:
                    state.touch[side] = (price, max(shown, level.quantity), executed)
                else:
                    state.touch[side] = (level.price, level.quantity, 0.0)
            imbalance = self._imbalance(book)
            if imbalance is not None and abs(imbalance) >= self.config.imbalance_threshold:
                side = "bids" if imbalance > 0 else "offers"
                self._event(
                    book,
                    "IMBALANCE",
                    f"Visible book {abs(imbalance):.0%} weighted to {side}",
                    {"imbalance": round(imbalance, 3)},
                    key=side,
                )

    @staticmethod
    def _imbalance(book: BookSnapshot, levels: int = 5) -> float | None:
        bid, ask = book.depth_quantity(BUY, levels), book.depth_quantity(SELL, levels)
        return (bid - ask) / (bid + ask) if bid + ask > 0 else None

    # ---- trades ---------------------------------------------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        instrument = self._instrument(tick.instrument_id)
        with self._lock:
            state = self._state(tick.instrument_id, tick.source)
            self._roll_day(state, instrument, tick.at)
            closed = self._add_to_bar(state, tick)
            if closed is not None:
                self._on_bar_close(state, closed, tick, instrument)
            signed = tick.quantity if tick.side == BUY else -tick.quantity if tick.side == SELL else 0.0
            state.cvd += signed
            state.day_volume += tick.quantity
            state.day_turnover += tick.quantity * tick.price
            state.day_turnover_sq += tick.quantity * tick.price * tick.price
            bucket = round(tick.price / tick_size(instrument)) * tick_size(instrument)
            state.at_price[round(bucket, 8)] = state.at_price.get(round(bucket, 8), 0.0) + tick.quantity
            self._large(state, tick, instrument)
            state.window.append((tick.at, tick.side, tick.quantity, tick.price))
            horizon = max(self.config.window_seconds, self.config.absorption_seconds)
            while state.window and (tick.at - state.window[0][0]).total_seconds() > horizon:
                state.window.popleft()
            self._aggressive(state, tick, instrument)
            self._absorption(state, tick, instrument)
            self._sweep(state, tick, instrument)
            self._iceberg(state, tick, instrument)
            state.sizes.append(tick.quantity)
            state.last_price = tick.price

    def _roll_day(self, state: _State, instrument, at: datetime) -> None:
        day = session_day(instrument, at)
        if state.day != day:
            state.day = day
            state.cvd = state.day_volume = state.day_turnover = state.day_turnover_sq = 0.0
            state.at_price = {}
            state.window.clear()

    def _add_to_bar(self, state: _State, tick: Tick) -> FlowBar | None:
        minute = tick.at.replace(second=0, microsecond=0)
        bar = state.bars[-1] if state.bars else None
        closed = None
        if bar is None or bar.start != minute:
            closed = bar if bar is not None and minute > bar.start else None
            if bar is not None and minute < bar.start:
                return None  # late tick for an old minute: ignore
            bar = FlowBar(minute, tick.price, tick.price, tick.price, tick.price)
            state.bars.append(bar)
        bar.high, bar.low, bar.close = max(bar.high, tick.price), min(bar.low, tick.price), tick.price
        bar.volume += tick.quantity
        bar.turnover += tick.quantity * tick.price
        bar.trades += 1
        bar.max_trade = max(bar.max_trade, tick.quantity)
        if tick.side == BUY:
            bar.buy += tick.quantity
        elif tick.side == SELL:
            bar.sell += tick.quantity
        return closed

    def _large(self, state: _State, tick: Tick, instrument) -> None:
        c = self.config
        if len(state.sizes) < c.large_min_samples or not tick.side:
            return
        typical = median(state.sizes)
        if typical <= 0 or tick.quantity < c.large_multiple * typical:
            return
        state.large.append((tick.at, tick.side, tick.quantity, tick.price))
        word = "buy" if tick.side == BUY else "sell"
        self._event(
            tick,
            "LARGE_TRADE",
            f"{quantity(tick.quantity)} {word} at {money(instrument, tick.price)} "
            f"({tick.quantity / typical:.0f}x the median trade)",
            {
                "side": tick.side,
                "quantity": tick.quantity,
                "price": tick.price,
                "size_vs_median": round(tick.quantity / typical, 1),
            },
            key=f"{tick.side}:{tick.at.isoformat()}",
        )
        recent = [
            x
            for x in state.large
            if x[1] == tick.side and (tick.at - x[0]).total_seconds() <= c.cluster_seconds
        ]
        if len(recent) >= c.cluster_count:
            total = sum(x[2] for x in recent)
            self._event(
                tick,
                "LARGE_TRADE_CLUSTER",
                f"{len(recent)} large {word} trades in {c.cluster_seconds:.0f}s, {quantity(total)} in all",
                {
                    "side": tick.side,
                    "count": len(recent),
                    "quantity": total,
                    "trades": [{"at": t.isoformat(), "quantity": q, "price": p} for t, _, q, p in recent],
                },
                key=tick.side,
            )

    def _window(self, state: _State, now: datetime, seconds: float) -> tuple[float, float, list]:
        rows = [x for x in state.window if (now - x[0]).total_seconds() <= seconds]
        buy = sum(q for _, s, q, _ in rows if s == BUY)
        sell = sum(q for _, s, q, _ in rows if s == SELL)
        return buy, sell, rows

    def _typical_minute_volume(self, state: _State) -> float:
        done = list(state.bars)[:-1][-self.config.spike_lookback :]
        return mean([b.volume for b in done])

    def _aggressive(self, state: _State, tick: Tick, instrument) -> None:
        c = self.config
        buy, sell, _ = self._window(state, tick.at, c.window_seconds)
        typical = self._typical_minute_volume(state)
        if typical <= 0 or buy + sell < typical * c.window_seconds / 60:
            return
        if buy >= c.aggressive_ratio * max(sell, 1e-9):
            self._event(
                tick,
                "AGGRESSIVE_BUYING",
                f"Aggressive buying {quantity(buy)} vs selling {quantity(sell)} in {c.window_seconds:.0f}s",
                {"buy": buy, "sell": sell, "ratio": round(buy / max(sell, 1e-9), 1) if sell else None},
            )
        elif sell >= c.aggressive_ratio * max(buy, 1e-9):
            self._event(
                tick,
                "AGGRESSIVE_SELLING",
                f"Aggressive selling {quantity(sell)} vs buying {quantity(buy)} in {c.window_seconds:.0f}s",
                {"buy": buy, "sell": sell, "ratio": round(sell / max(buy, 1e-9), 1) if buy else None},
            )

    def _absorption(self, state: _State, tick: Tick, instrument) -> None:
        c = self.config
        buy, sell, rows = self._window(state, tick.at, c.absorption_seconds)
        typical = self._typical_minute_volume(state)
        if len(rows) < 3 or typical <= 0:
            return
        first_price = rows[0][3]
        move_ticks = (tick.price - first_price) / tick_size(instrument)
        heavy = typical * c.absorption_seconds / 60 * 1.5
        if sell >= heavy and sell >= 2 * buy and move_ticks >= -1:
            self._event(
                tick,
                "ABSORPTION",
                f"Selling pressure absorbed: {quantity(sell)} sold into the bid in "
                f"{c.absorption_seconds:.0f}s, price unchanged near {money(instrument, tick.price)}",
                {
                    "side": "BID",
                    "aggressive_volume": sell,
                    "opposite_volume": buy,
                    "price_change_ticks": round(move_ticks, 1),
                    "price": tick.price,
                },
                key="BID",
            )
        elif buy >= heavy and buy >= 2 * sell and move_ticks <= 1:
            self._event(
                tick,
                "ABSORPTION",
                f"Buying pressure absorbed: {quantity(buy)} bought from the offer in "
                f"{c.absorption_seconds:.0f}s, price unchanged near {money(instrument, tick.price)}",
                {
                    "side": "ASK",
                    "aggressive_volume": buy,
                    "opposite_volume": sell,
                    "price_change_ticks": round(move_ticks, 1),
                    "price": tick.price,
                },
                key="ASK",
            )

    def _sweep(self, state: _State, tick: Tick, instrument) -> None:
        previous = state.prev_book
        if previous is None or not tick.side:
            return
        levels = previous.asks if tick.side == BUY else previous.bids
        cleared = [
            lv for lv in levels if (lv.price <= tick.price if tick.side == BUY else lv.price >= tick.price)
        ]
        if len(cleared) < self.config.sweep_levels:
            return
        shown = sum(lv.quantity for lv in cleared)
        if tick.quantity < 0.5 * shown:
            return
        word = "Buy" if tick.side == BUY else "Sell"
        self._event(
            tick,
            "SWEEP",
            f"{word} sweep through {len(cleared)} levels to {money(instrument, tick.price)}",
            {
                "side": tick.side,
                "levels": len(cleared),
                "from_price": cleared[0].price,
                "to_price": tick.price,
                "quantity": tick.quantity,
                "displayed": shown,
            },
            key=tick.side,
        )

    def _iceberg(self, state: _State, tick: Tick, instrument) -> None:
        side = "BID" if tick.side == SELL else "ASK" if tick.side == BUY else None
        if side is None or side not in state.touch:
            return
        price, shown, executed = state.touch[side]
        if price != tick.price:
            return
        executed += tick.quantity
        state.touch[side] = (price, shown, executed)
        typical = median(state.sizes) if state.sizes else 0.0
        if shown > 0 and executed >= self.config.iceberg_multiple * shown and executed >= 10 * typical:
            self._event(
                tick,
                "ICEBERG_LIKE",
                f"{quantity(executed)} traded at {money(instrument, price)} where at "
                f"most {quantity(shown)} was ever shown ({side.lower()})",
                {
                    "side": side,
                    "price": price,
                    "executed": executed,
                    "max_displayed": shown,
                    "ratio": round(executed / shown, 1),
                },
                key=f"{side}:{price}",
            )

    def _on_bar_close(self, state: _State, bar: FlowBar, tick: Tick, instrument) -> None:
        c = self.config
        history = list(state.bars)[:-2][-c.spike_lookback :]  # completed bars before the one just closed
        if len(history) >= 5:
            avg_volume = mean([b.volume for b in history])
            if avg_volume > 0 and bar.volume >= c.spike_multiple * avg_volume:
                self._event(
                    tick,
                    "VOLUME_SPIKE",
                    f"{quantity(bar.volume)} traded in a minute, "
                    f"{bar.volume / avg_volume:.1f}x the recent average",
                    {"volume": bar.volume, "average": avg_volume, "bar": bar.to_dict()},
                    at=bar.start + timedelta(minutes=1),
                )
            ranges = [b.high - b.low for b in history if b.high > b.low]
            typical_range = median(ranges)
            if typical_range > 0 and bar.high - bar.low >= c.spike_multiple * typical_range:
                self._event(
                    tick,
                    "VOLATILITY_SPIKE",
                    f"One-minute range {money(instrument, bar.high - bar.low)}, "
                    f"{(bar.high - bar.low) / typical_range:.1f}x usual",
                    {"range": bar.high - bar.low, "usual": typical_range, "bar": bar.to_dict()},
                    at=bar.start + timedelta(minutes=1),
                )
            highs = [b.high for b in history]
            lows = [b.low for b in history]
            recent = history[-5:]
            if bar.high > max(highs):
                push = mean([b.delta for b in recent if b.delta > 0])
                if bar.delta <= 0 or (push > 0 and bar.delta < 0.3 * push):
                    self._event(
                        tick,
                        "EXHAUSTION",
                        f"New high {money(instrument, bar.high)} on weak buying "
                        f"(delta {quantity(bar.delta) if bar.delta >= 0 else '-' + quantity(-bar.delta)})",
                        {"direction": "UP", "high": bar.high, "delta": bar.delta, "recent_push": push},
                        key="UP",
                    )
            elif bar.low < min(lows):
                push = mean([-b.delta for b in recent if b.delta < 0])
                if bar.delta >= 0 or (push > 0 and -bar.delta < 0.3 * push):
                    self._event(
                        tick,
                        "EXHAUSTION",
                        f"New low {money(instrument, bar.low)} on weak selling",
                        {"direction": "DOWN", "low": bar.low, "delta": bar.delta, "recent_push": push},
                        key="DOWN",
                    )
        deltas = [b.delta for b in list(state.bars)[:-1]]
        if len(deltas) >= 20:
            before, after = sum(deltas[-20:-5]), sum(deltas[-5:])
            if (
                before
                and after
                and math.copysign(1, before) != math.copysign(1, after)
                and abs(after) >= 0.25 * abs(before)
            ):
                direction = "buyers" if after > 0 else "sellers"
                self._event(
                    tick,
                    "ORDER_FLOW_REVERSAL",
                    f"Order flow turned to {direction}: 5-minute delta "
                    f"{after:+,.0f} after {before:+,.0f} over the prior 15",
                    {"delta_before": before, "delta_after": after},
                    key=direction,
                )

    def _event(self, obj, kind: str, title: str, data: dict, *, key: str = "", at: datetime | None = None):
        at = at or (obj.at if isinstance(obj, Tick) else obj.exchange_ts)
        self._emit(MarketEvent(obj.instrument_id, kind, at, title, dict(data), source=obj.source, key=key))

    # ---- reading --------------------------------------------------------------------------------------

    def snapshot(self, instrument_id: str, source: str | None = None) -> dict[str, Any] | None:
        """Current order-flow metrics; the source with the most volume today when none is given."""
        with self._lock:
            candidates = {s: st for (iid, s), st in self._states.items() if iid == instrument_id}
            if not candidates:
                return None
            if source is None or source not in candidates:
                source = max(candidates, key=lambda s: candidates[s].day_volume)
            state = candidates[source]
            bars = list(state.bars)
            now = bars[-1].start + timedelta(minutes=1) if bars else None
            buy_1m = sell_1m = buy_5m = sell_5m = 0.0
            if now is not None:
                for t, s, q, _ in state.window:
                    if (now - t).total_seconds() <= 60:
                        buy_1m += q if s == BUY else 0
                        sell_1m += q if s == SELL else 0
            for b in bars[-5:]:
                buy_5m += b.buy
                sell_5m += b.sell
            book = state.last_book
            vwap = state.day_turnover / state.day_volume if state.day_volume else None
            variance = (
                state.day_turnover_sq / state.day_volume - vwap * vwap if state.day_volume and vwap else None
            )
            typical_volume = mean([b.volume for b in bars[:-1][-20:]])
            return {
                "instrument_id": instrument_id,
                "source": source,
                "sources": sorted(candidates),
                "last_price": state.last_price,
                "cvd": round(state.cvd, 4),
                "day_volume": round(state.day_volume, 4),
                "vwap": vwap,
                "vwap_std": math.sqrt(variance) if variance and variance > 0 else None,
                "delta_1m": round(buy_1m - sell_1m, 4),
                "delta_5m": round(buy_5m - sell_5m, 4),
                "buy_5m": round(buy_5m, 4),
                "sell_5m": round(sell_5m, 4),
                "aggressive_ratio_5m": round(buy_5m / sell_5m, 3) if sell_5m else None,
                "volume_ratio": (
                    round(bars[-1].volume / typical_volume, 2) if bars and typical_volume else None
                ),
                "imbalance_top5": None if book is None else self._imbalance(book),
                "exchange_buy_sell_ratio": (
                    round(book.total_buy_quantity / book.total_sell_quantity, 3)
                    if book is not None and book.total_buy_quantity and book.total_sell_quantity
                    else None
                ),
                "spread": None if book is None else book.spread,
                "spread_usual": state.spread_avg,
                "median_trade": median(state.sizes) if state.sizes else None,
                "large_trades": [
                    {"at": t.isoformat(), "side": s, "quantity": q, "price": p}
                    for t, s, q, p in list(state.large)[-20:]
                ],
                "bars": [b.to_dict() for b in bars[-120:]],
                "estimated": True,
            }

    def volume_at_price(self, instrument_id: str, source: str | None = None) -> dict[float, float]:
        with self._lock:
            candidates = {s: st for (iid, s), st in self._states.items() if iid == instrument_id}
            if not candidates:
                return {}
            if source is None or source not in candidates:
                source = max(candidates, key=lambda s: candidates[s].day_volume)
            return dict(candidates[source].at_price)

    def bars(self, instrument_id: str, source: str | None = None) -> list[FlowBar]:
        with self._lock:
            candidates = {s: st for (iid, s), st in self._states.items() if iid == instrument_id}
            if not candidates:
                return []
            if source is None or source not in candidates:
                source = max(candidates, key=lambda s: candidates[s].day_volume)
            return list(candidates[source].bars)
