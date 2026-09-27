"""Latest market state, reference prices and staleness (BR-20-04, FR-20044)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from jdquant.core.clock import Clock
from jdquant.core.events import EventBus
from jdquant.marketdata.records import Candle, Quote, Trade


class FeedStatus(StrEnum):
    LIVE = "LIVE"
    STALE = "STALE"
    DOWN = "DOWN"


@dataclass
class _State:
    last_trade: Trade | None = None
    quote: Quote | None = None
    mark: Decimal | None = None
    updated_at: datetime | None = None
    status: FeedStatus = FeedStatus.DOWN


class MarketDataCache:
    def __init__(
        self, clock: Clock, bus: EventBus | None = None, stale_after: timedelta = timedelta(seconds=10)
    ):
        self._clock = clock
        self._bus = bus
        self._stale_after = stale_after
        self._state: dict[str, _State] = {}

    def on_trade(self, trade: Trade) -> None:
        state = self._touch(trade.instrument_id)
        state.last_trade = trade

    def on_quote(self, quote: Quote) -> None:
        state = self._touch(quote.instrument_id)
        state.quote = None if quote.is_crossed else quote

    def on_mark(self, instrument_id: str, price: Decimal) -> None:
        self._touch(instrument_id).mark = price

    def on_candle(self, candle: Candle) -> None:
        self.on_trade(Trade(candle.instrument_id, candle.close_ts, candle.close, candle.volume))

    def reference_price(self, instrument_id: str) -> Decimal | None:
        """Mark if available, else valid mid, else last trade (BR-20-04)."""
        state = self._state.get(instrument_id)
        if state is None:
            return None
        if state.mark is not None:
            return state.mark
        if state.quote is not None:
            return state.quote.mid
        return state.last_trade.price if state.last_trade else None

    def status(self, instrument_id: str) -> FeedStatus:
        state = self._state.get(instrument_id)
        if state is None or state.updated_at is None:
            return FeedStatus.DOWN
        is_stale = self._clock.now() - state.updated_at > self._stale_after
        new_status = FeedStatus.STALE if is_stale else FeedStatus.LIVE
        previous, state.status = state.status, new_status
        if (
            self._bus is not None
            and previous is not new_status
            and FeedStatus.STALE in (previous, new_status)
        ):
            event = "marketdata.stale" if is_stale else "marketdata.recovered"
            self._bus.publish(
                event, {"instrument_id": instrument_id}, producer="mde", partition_key=instrument_id
            )
        return new_status

    def is_stale(self, instrument_id: str) -> bool:
        return self.status(instrument_id) is not FeedStatus.LIVE

    def _touch(self, instrument_id: str) -> _State:
        state = self._state.setdefault(instrument_id, _State())
        state.updated_at = self._clock.now()
        if state.status is not FeedStatus.LIVE:
            self.status(instrument_id)
        return state
