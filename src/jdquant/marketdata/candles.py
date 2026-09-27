"""Candle aggregation from trades (FR-20060 – FR-20064)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from jdquant.marketdata.records import Candle, Trade

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def bucket_start(ts: datetime, interval_seconds: int) -> datetime:
    """UTC-aligned interval start (FR-20061)."""
    elapsed = int((ts - _EPOCH).total_seconds())
    return _EPOCH + timedelta(seconds=elapsed - elapsed % interval_seconds)


class CandleAggregator:
    """Builds candles for one instrument and interval from time-ordered trades."""

    def __init__(self, instrument_id: str, interval_seconds: int, *, emit_empty: bool = True):
        self.instrument_id = instrument_id
        self.interval = interval_seconds
        self.emit_empty = emit_empty
        self._open_ts: datetime | None = None
        self._o = self._h = self._l = self._c = Decimal(0)
        self._volume = Decimal(0)
        self._quote_volume = Decimal(0)
        self._count = 0
        self._last_close: Decimal | None = None

    def on_trade(self, trade: Trade) -> list[Candle]:
        start = bucket_start(trade.exchange_ts, self.interval)
        closed = self._close_until(start)
        if self._open_ts is None or self._count == 0:
            self._open_ts = start
            self._o = self._h = self._l = trade.price
        self._h = max(self._h, trade.price)
        self._l = min(self._l, trade.price)
        self._c = trade.price
        self._volume += trade.quantity
        self._quote_volume += trade.quantity * trade.price
        self._count += 1
        return closed

    def flush(self, now: datetime) -> list[Candle]:
        """Close every interval that ended at or before `now`."""
        return self._close_until(bucket_start(now, self.interval))

    def current(self) -> Candle | None:
        if self._open_ts is None or self._count == 0:
            return None
        return self._build(is_closed=False)

    def _close_until(self, start: datetime) -> list[Candle]:
        closed: list[Candle] = []
        step = timedelta(seconds=self.interval)
        while self._open_ts is not None and self._open_ts < start:
            if self._count:
                candle = self._build(is_closed=True)
                closed.append(candle)
                self._last_close = candle.close
            elif self.emit_empty and self._last_close is not None:
                closed.append(self._empty(self._open_ts, self._last_close))
            self._reset(self._open_ts + step)
        return closed

    def _build(self, *, is_closed: bool) -> Candle:
        assert self._open_ts is not None
        return Candle(
            instrument_id=self.instrument_id,
            interval_seconds=self.interval,
            open_ts=self._open_ts,
            close_ts=self._open_ts + timedelta(seconds=self.interval),
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
            volume=self._volume,
            quote_volume=self._quote_volume,
            trade_count=self._count,
            vwap=self._quote_volume / self._volume if self._volume else None,
            is_closed=is_closed,
        )

    def _empty(self, open_ts: datetime, price: Decimal) -> Candle:
        """Empty interval uses the previous close with zero volume (FR-20063)."""
        return Candle(
            instrument_id=self.instrument_id,
            interval_seconds=self.interval,
            open_ts=open_ts,
            close_ts=open_ts + timedelta(seconds=self.interval),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal(0),
        )

    def _reset(self, next_open: datetime) -> None:
        self._open_ts = next_open
        self._volume = Decimal(0)
        self._quote_volume = Decimal(0)
        self._count = 0
