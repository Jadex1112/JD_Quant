"""Deterministic synthetic candles for demos and tests."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from decimal import Decimal

from jdquant.core.types import Side
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Candle

_BUY = Side.BUY


def random_walk_candles(
    instrument: Instrument,
    start: datetime,
    count: int,
    *,
    interval_seconds: int = 3600,
    start_price: Decimal = Decimal(100),
    volatility: float = 0.01,
    drift: float = 0.0,
    seed: int = 7,
) -> list[Candle]:
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = float(start_price)
    step = timedelta(seconds=interval_seconds)
    for i in range(count):
        open_ = price
        path = [open_]
        for _ in range(4):
            path.append(path[-1] * math.exp(drift / 4 + rng.gauss(0, volatility / 2)))
        close = path[-1]
        high, low = max(path), min(path)
        quantize = instrument.round_price_passive
        o, c = quantize(Decimal(str(open_)), _BUY), quantize(Decimal(str(close)), _BUY)
        h = max(quantize(Decimal(str(high)), _BUY), o, c)
        low_ = min(quantize(Decimal(str(low)), _BUY), o, c)
        open_ts = start + step * i
        candles.append(
            Candle(
                instrument_id=instrument.instrument_id,
                interval_seconds=interval_seconds,
                open_ts=open_ts,
                close_ts=open_ts + step,
                open=o,
                high=h,
                low=low_,
                close=c,
                volume=Decimal(rng.randint(100, 1000)),
            )
        )
        price = close
    return candles
