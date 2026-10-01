"""Simulated order books for demos (JDQ_DEMO_FEED=1), published as source "SIMULATED".

For instruments under depth watch that no broker covers, it produces a live-looking book: a random
walk, 20 levels a side, trades that hit the bid or lift the offer in runs, occasional large trades,
and every few minutes a large wall that is later either withdrawn or traded away. The point is to show
what the order-book and flow analytics do; nothing here is market data, and every screen says so.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from jdquant.marketdata.book import BookSnapshot, Level
from jdquant.marketdata.live import YEAR_SECONDS, annual_volatility

SOURCE = "SIMULATED"


@dataclass
class _Wall:
    side: str
    price: float
    quantity: float
    orders: int
    until: datetime
    fate: str  # WITHDRAW or CONSUME


@dataclass
class _Sim:
    mid: float
    tick: float
    typical: float
    volume: float = 0.0
    bias: float = 0.0  # persistent buy/sell pressure, -1..1
    wall: _Wall | None = None
    next_wall: datetime | None = None
    levels: dict[str, list[float]] = field(default_factory=dict)


class SimulatedDepthFeed:
    def __init__(
        self,
        publish: Callable[[BookSnapshot], None],
        instruments: Callable[[], list],
        start_price: Callable[[object], float],
        now: Callable[[], datetime],
        *,
        interval: float = 1.0,
        levels: int = 20,
        seed: int | None = None,
    ):
        self._publish = publish
        self._instruments = instruments
        self._start_price = start_price
        self._now = now
        self.interval = interval
        self.levels = levels
        self._rng = random.Random(seed)
        self._sims: dict[str, _Sim] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def covers(self, instrument_id: str) -> bool:
        return instrument_id in self._sims

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="simulated-depth", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # a demo must never take the platform down
                pass

    def tick(self) -> None:
        now = self._now()
        wanted = {i.instrument_id: i for i in self._instruments()}
        for iid in list(self._sims):
            if iid not in wanted:
                del self._sims[iid]
        for iid, instrument in wanted.items():
            sim = self._sims.get(iid)
            if sim is None:
                sim = self._sims[iid] = self._new(instrument, now)
            self._step(instrument, sim, now)

    def _new(self, instrument, now: datetime) -> _Sim:
        price = self._start_price(instrument)
        tick = float(instrument.tick_size) or 0.01
        seed = int(hashlib.sha256(instrument.instrument_id.encode()).hexdigest()[:8], 16)
        typical = max(1.0, round(200_000 / max(price, 1e-6))) if price > 1 else 50_000.0
        return _Sim(
            round(price / tick) * tick,
            tick,
            typical,
            volume=float(seed % 100_000) * typical / 10,
            next_wall=now + timedelta(seconds=20),
        )

    def _step(self, instrument, sim: _Sim, now: datetime) -> None:
        rng = self._rng
        tick = sim.tick
        wall = self._wall(sim, now)
        consuming = wall is not None and wall.fate == "CONSUME" and now >= wall.until - timedelta(seconds=20)
        if consuming:
            # walk the price to the wall, then trade into it
            target = wall.price + (tick / 2 if wall.side == "BID" else -tick / 2)
            sim.mid = (
                sim.mid + (tick if target > sim.mid else -tick) if abs(target - sim.mid) > tick else target
            )
            sim.bias = -0.8 if wall.side == "BID" else 0.8
        else:
            sigma = annual_volatility(instrument) * math.sqrt(self.interval / YEAR_SECONDS)
            sim.bias = max(-1.0, min(1.0, 0.92 * sim.bias + rng.gauss(0, 0.25)))
            sim.mid *= 1 + sim.bias * sigma * 0.6 + rng.gauss(0, sigma)
            sim.mid = max(tick * 10, sim.mid)
        best_bid = math.floor(sim.mid / tick) * tick
        best_ask = best_bid + tick
        if wall is not None and wall.fate == "WITHDRAW":
            crossed = wall.price >= best_bid if wall.side == "BID" else wall.price <= best_ask
            if crossed:  # price ran into it before it expired: it is pulled instead
                sim.wall, wall = None, None
                sim.next_wall = now + timedelta(seconds=rng.uniform(60, 180))
        buy_share = 0.5 + 0.35 * sim.bias
        trades = rng.randint(0, 6)
        traded = 0.0
        last = best_ask if rng.random() < buy_share else best_bid
        for _ in range(trades):
            size = sim.typical * rng.lognormvariate(-1.2, 0.9)
            if rng.random() < 0.02:
                size *= rng.uniform(8, 20)  # an occasional large trade
            traded += size
        if consuming and wall is not None:
            touch = best_bid if wall.side == "BID" else best_ask
            if abs(touch - wall.price) < tick / 2:
                take = wall.quantity * rng.uniform(0.3, 0.6)
                wall.quantity -= take
                traded += take
                last = wall.price
                if wall.quantity < sim.typical:
                    sim.wall = None
                    sim.next_wall = now + timedelta(seconds=rng.uniform(60, 180))
                    wall = None
        sim.volume += round(traded)
        bids = self._side(sim, best_bid, -1, wall if wall and wall.side == "BID" else None)
        asks = self._side(sim, best_ask, 1, wall if wall and wall.side == "ASK" else None)
        self._publish(
            BookSnapshot(
                instrument.instrument_id,
                SOURCE,
                now,
                now,
                bids=bids,
                asks=asks,
                last_price=round(last, 8),
                last_quantity=round(traded / max(1, trades)) if trades else None,
                volume=sim.volume,
                total_buy_quantity=round(sum(lv.quantity for lv in bids) * rng.uniform(3, 6)),
                total_sell_quantity=round(sum(lv.quantity for lv in asks) * rng.uniform(3, 6)),
                capacity=self.levels,
                meta={"simulated": True},
            )
        )

    def _wall(self, sim: _Sim, now: datetime) -> _Wall | None:
        rng = self._rng
        if sim.wall is not None and now >= sim.wall.until:
            sim.wall = None
            sim.next_wall = now + timedelta(seconds=rng.uniform(60, 180))
        if sim.wall is None and sim.next_wall is not None and now >= sim.next_wall:
            side = "BID" if rng.random() < 0.5 else "ASK"
            away = rng.randint(3, 8) * sim.tick
            base = math.floor(sim.mid / sim.tick) * sim.tick
            price = base - away if side == "BID" else base + sim.tick + away
            sim.wall = _Wall(
                side,
                round(price, 8),
                sim.typical * rng.uniform(60, 200),
                rng.randint(40, 220),
                now + timedelta(seconds=rng.uniform(40, 100)),
                "WITHDRAW" if rng.random() < 0.6 else "CONSUME",
            )
            sim.next_wall = None
        return sim.wall

    def _side(self, sim: _Sim, best: float, direction: int, wall: _Wall | None) -> tuple[Level, ...]:
        rng = self._rng
        out = []
        for k in range(self.levels):
            price = round((best + direction * k * sim.tick) / sim.tick) * sim.tick
            qty = sim.typical * rng.lognormvariate(0, 0.6) * (1 + k * 0.05)
            orders = max(1, int(qty / sim.typical * rng.uniform(2, 6)))
            if wall is not None and abs(price - wall.price) < sim.tick / 2:
                qty, orders = wall.quantity, wall.orders
            out.append(Level(round(price, 8), round(qty), orders))
        return tuple(out)
