"""Data quality across brokers: when feeds disagree, say why instead of silently merging them.

Every book from every source is compared with the other sources' latest view of the same instrument.
A difference larger than one tick (or 0.05%) is classified:

- **Timing difference**: one feed shows a price the other showed a moment ago; it is lagging.
- **Stale quote**: one feed has stopped updating while others move.
- **Discrepancy**: both feeds are live and keep disagreeing beyond a few seconds; this is reported as
  an event because one of them is wrong (or they are not quoting the same thing).

Agreement across independent feeds raises confidence in an observation; it never adds quantities.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from jdquant.intelligence.common import money, tick_size
from jdquant.intelligence.events import MarketEvent
from jdquant.marketdata.book import BookSnapshot
from jdquant.marketdata.instruments import Instrument

STALE_AFTER = timedelta(seconds=10)
TIMING_LOOKBACK = timedelta(seconds=5)
DISCREPANCY_AFTER = timedelta(seconds=5)


def _price(book: BookSnapshot) -> float | None:
    return book.mid if book.mid is not None else book.last_price


@dataclass
class _Pair:
    since: datetime | None = None
    reported: bool = False


@dataclass
class _Instrument:
    history: dict[str, deque] = field(default_factory=dict)  # source -> (received, price)
    latest: dict[str, BookSnapshot] = field(default_factory=dict)
    pairs: dict[tuple[str, str], _Pair] = field(default_factory=dict)
    verdicts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


class DataQuality:
    def __init__(
        self,
        emit: Callable[[MarketEvent], MarketEvent | None],
        instrument: Callable[[str], Instrument | None],
        now: Callable[[], datetime],
    ):
        self._emit = emit
        self._instrument = instrument
        self._now = now
        self._state: dict[str, _Instrument] = {}
        self._lock = threading.Lock()
        self.discrepancies = 0

    def on_book(self, book: BookSnapshot, previous: BookSnapshot | None) -> None:
        price = _price(book)
        if price is None:
            return
        instrument = self._instrument(book.instrument_id)
        with self._lock:
            state = self._state.setdefault(book.instrument_id, _Instrument())
            hist = state.history.setdefault(book.source, deque(maxlen=200))
            hist.append((book.received_ts, price))
            state.latest[book.source] = book
            for other, other_book in state.latest.items():
                if other == book.source:
                    continue
                self._compare(state, book, other_book, instrument)

    def _compare(self, state: _Instrument, book: BookSnapshot, other: BookSnapshot, instrument) -> None:
        pair_key = tuple(sorted((book.source, other.source)))
        pair = state.pairs.setdefault(pair_key, _Pair())
        a, b = _price(book), _price(other)
        if a is None or b is None:
            return
        tolerance = max(tick_size(instrument), 0.0005 * a)
        diff = a - b
        age_other = book.received_ts - other.received_ts
        verdict: dict[str, Any] = {
            "sources": list(pair_key),
            "prices": {book.source: a, other.source: b},
            "difference": diff,
            "checked_at": book.received_ts.isoformat(),
        }
        if abs(diff) <= tolerance:
            verdict["state"] = "AGREE"
            pair.since, pair.reported = None, False
        elif age_other > STALE_AFTER:
            verdict["state"] = "STALE"
            verdict["detail"] = f"{other.source} has not updated for {age_other.total_seconds():.0f}s"
            pair.since = None
        elif self._recent(state, other.source, a, book.received_ts, tolerance) or self._recent(
            state, book.source, b, book.received_ts, tolerance
        ):
            verdict["state"] = "TIMING"
            verdict["detail"] = "One feed shows a price the other showed seconds ago: a delay, not a conflict"
            pair.since = None
        else:
            verdict["state"] = "DISCREPANCY"
            pair.since = pair.since or book.received_ts
            lasting = book.received_ts - pair.since
            verdict["detail"] = f"Both feeds live and disagreeing for {lasting.total_seconds():.0f}s"
            if lasting >= DISCREPANCY_AFTER and not pair.reported:
                pair.reported = True
                self.discrepancies += 1
                self._emit(
                    MarketEvent(
                        book.instrument_id,
                        "DATA_DISCREPANCY",
                        book.exchange_ts,
                        f"{book.source} {money(instrument, a)} vs {other.source} {money(instrument, b)} "
                        f"for {lasting.total_seconds():.0f}s",
                        {**verdict, "tolerance": tolerance},
                        key="/".join(pair_key),
                    )
                )
        state.verdicts[pair_key] = verdict

    @staticmethod
    def _recent(state: _Instrument, source: str, price: float, now: datetime, tolerance: float) -> bool:
        """Whether the source showed this price within the last few seconds."""
        for at, value in reversed(state.history.get(source, ())):
            if now - at > TIMING_LOOKBACK:
                break
            if abs(value - price) <= tolerance:
                return True
        return False

    def report(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        now = self._now()
        with self._lock:
            if instrument_id is None:
                items = list(self._state.items())
            else:
                items = [(instrument_id, self._state.get(instrument_id))]
            out = []
            for iid, state in items:
                if state is None:
                    continue
                sources = {
                    s: {
                        "price": _price(b),
                        "bid": b.best_bid.price if b.best_bid else None,
                        "ask": b.best_ask.price if b.best_ask else None,
                        "age_seconds": round((now - b.received_ts).total_seconds(), 1),
                        "exchange_time": b.exchange_ts.isoformat(),
                    }
                    for s, b in state.latest.items()
                }
                verdicts = list(state.verdicts.values())
                worst = "AGREE"
                for level in ("DISCREPANCY", "STALE", "TIMING"):
                    if any(v["state"] == level for v in verdicts):
                        worst = level
                        break
                agreeing = {s for v in verdicts if v["state"] == "AGREE" for s in v["sources"]}
                out.append(
                    {
                        "instrument_id": iid,
                        "sources": sources,
                        "state": worst if len(sources) > 1 else "SINGLE_SOURCE",
                        "confirmed_by": sorted(agreeing),
                        "comparisons": verdicts,
                    }
                )
            return out
