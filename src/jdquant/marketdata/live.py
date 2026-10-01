"""Live prices for charts: one-minute candles built from every quote, and a fan-out to streaming clients.

Every quote the platform sees (broker polling, manual quotes, the optional simulated feed) updates the
current one-minute bar of its instrument. Charts load history once, then receive each quote as it happens.
"""

from __future__ import annotations

import hashlib
import math
import queue
import random
import threading
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.marketdata.records import Candle, Quote

MINUTE = 60
YEAR_SECONDS = 365 * 86400


def annual_volatility(instrument) -> float:
    """Annual volatility used for simulated prices, a little above typical so the demo visibly moves."""
    from jdquant.marketdata.instruments import AssetClass

    match instrument.asset_class:
        case AssetClass.FX:
            return 0.15
        case AssetClass.COMMODITY:
            return 0.30
        case AssetClass.CRYPTO_SPOT:
            return 0.25 if instrument.base_asset in ("PAXG", "XAUT") else 0.80
        case _:
            return 0.40


def _floor(at: datetime, seconds: int) -> datetime:
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, at.tzinfo)


class LiveMarket:
    def __init__(self, keep_minutes: int = 1440):
        self._bars: dict[str, deque[list]] = {}  # [open_ts, o, h, l, c] per minute
        self._keep = keep_minutes
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.simulated: set[str] = set()  # instruments priced by the simulated feed
        self._watching: dict[int, set[str]] = {}

    def on_quote(self, quote: Quote) -> None:
        if quote.is_crossed:
            return
        price = float(quote.mid)
        minute = _floor(quote.exchange_ts, MINUTE)
        with self._lock:
            bars = self._bars.setdefault(quote.instrument_id, deque(maxlen=self._keep))
            if bars and bars[-1][0] == minute:
                bar = bars[-1]
                bar[2], bar[3], bar[4] = max(bar[2], price), min(bar[3], price), price
            elif not bars or bars[-1][0] < minute:
                bars.append([minute, price, price, price, price])
            subscribers = list(self._subscribers)
        event = {
            "instrument_id": quote.instrument_id,
            "time": quote.exchange_ts.isoformat(),
            "bid": str(quote.bid_price),
            "ask": str(quote.ask_price),
            "price": price,
            "simulated": quote.instrument_id in self.simulated,
        }
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:  # a slow client drops quotes rather than holding up the platform
                pass

    def candles(self, instrument_id: str, interval_seconds: int, limit: int) -> list[dict[str, Any]]:
        """Minute bars aggregated to `interval_seconds` (a multiple of 60)."""
        interval = max(MINUTE, interval_seconds - interval_seconds % MINUTE)
        with self._lock:
            bars = list(self._bars.get(instrument_id, ()))
        out: list[dict[str, Any]] = []
        for open_ts, o, h, low, c in bars:
            bucket = _floor(open_ts, interval)
            if out and out[-1]["open_ts"] == bucket:
                last = out[-1]
                last["high"], last["low"], last["close"] = max(last["high"], h), min(last["low"], low), c
            else:
                out.append({"open_ts": bucket, "open": o, "high": h, "low": low, "close": c})
        return out[-limit:]

    def subscribe(self, instruments: set[str] | None = None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
            self._watching[id(q)] = set(instruments or ())
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
            self._watching.pop(id(q), None)

    def watched(self) -> set[str]:
        """Instruments open on someone's chart, so the poller keeps fetching their prices."""
        with self._lock:
            return set().union(*self._watching.values()) if self._watching else set()


def candle_dict(c: Candle) -> dict[str, Any]:
    return {
        "open_ts": c.open_ts,
        "open": float(c.open),
        "high": float(c.high),
        "low": float(c.low),
        "close": float(c.close),
    }


class SimulatedFeed:
    """Random-walk quotes for instruments without a live source, for demos (JDQ_DEMO_FEED=1).

    Every price it produces is marked simulated in the stream and on charts.
    """

    def __init__(
        self, platform, publish, *, has_source, live: LiveMarket | None = None, interval: float = 1.0
    ):
        self._p = platform
        self._live = live
        self._publish = publish
        self._has_source = has_source
        self.interval = interval
        self._prices: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="simulated-feed", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()

    def _start_price(self, instrument) -> float:
        from jdquant.platform import DEMO_PRICES

        iid = instrument.instrument_id
        reference = self._p.market.reference_price(iid) or DEMO_PRICES.get(iid)
        seed = int(hashlib.sha256(iid.encode()).hexdigest()[:8], 16)
        return float(reference) if reference else 100.0 + seed % 2000

    def tick(self) -> None:
        now = self._p.clock.now()
        for instrument in self._p.instruments.search():
            if instrument.is_future or self._has_source(instrument.instrument_id):
                continue
            iid = instrument.instrument_id
            if self._live is not None:
                self._live.simulated.add(iid)
            price = self._prices.get(iid) or self._start_price(instrument)
            price *= 1 + random.gauss(
                0, annual_volatility(instrument) * math.sqrt(self.interval / YEAR_SECONDS)
            )
            self._prices[iid] = price
            tick = float(instrument.tick_size)
            mid = round(price / tick) * tick
            bid, ask = Decimal(str(round(mid - tick, 8))), Decimal(str(round(mid + tick, 8)))
            self._publish(Quote(iid, now, bid, Decimal(1), ask, Decimal(1)))

    def backfill(self, live: LiveMarket, minutes: int = 240) -> None:
        """Give charts some recent history to start from."""
        now = self._p.clock.now()
        for instrument in self._p.instruments.search():
            if instrument.is_future or self._has_source(instrument.instrument_id):
                continue
            iid = instrument.instrument_id
            live.simulated.add(iid)
            price = self._start_price(instrument)
            rng = random.Random(int(hashlib.sha256(iid.encode()).hexdigest()[:8], 16))
            sigma = annual_volatility(instrument) * math.sqrt(60 / YEAR_SECONDS)
            path = [price]
            for _ in range(minutes):
                path.append(path[-1] * (1 + rng.gauss(0, sigma)))
            path.reverse()  # walk backwards from today's price
            tick = float(instrument.tick_size)
            for k, p in enumerate(path):
                at = now - timedelta(minutes=len(path) - k)
                mid = round(p / tick) * tick
                live.on_quote(
                    Quote(
                        iid,
                        at,
                        Decimal(str(round(mid - tick, 8))),
                        Decimal(1),
                        Decimal(str(round(mid + tick, 8))),
                        Decimal(1),
                    )
                )
            self._prices[iid] = path[-1]
