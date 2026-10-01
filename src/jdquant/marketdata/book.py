"""Normalized order-book snapshots and trade ticks, the common language of every market-data source.

Each broker feed (Dhan, Fyers, Kotak Neo, Zerodha, ...) and each REST depth poll is translated into a
`BookSnapshot`: the visible bids and asks with quantities and order counts, plus the last trade, the
day's volume and open interest when the source sends them. Snapshots from different sources are kept
apart (see `MarketDataHub`): two brokers showing 585,858 shares bid at 263.00 are two observations of
the same exchange book, never 1,171,716 shares.

Indian broker feeds do not publish every exchange trade. They send the last traded price and quantity
and the cumulative day volume, so trades between two snapshots are inferred from the volume change and
classified as buyer- or seller-initiated by where they printed relative to the previous best bid and
ask (the quote rule), falling back to the direction of the price move (the tick rule). `Tick.inferred`
marks them; analytics built on them are estimates, not the exchange's trade tape.

Prices and quantities here are floats: this layer feeds analytics, not the ledger, which stays in Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from jdquant.marketdata.records import Quote

BUY, SELL = "BUY", "SELL"


@dataclass(frozen=True)
class Level:
    price: float
    quantity: float
    orders: int | None = None


@dataclass(frozen=True)
class BookSnapshot:
    instrument_id: str
    source: str  # the broker or feed that observed it, e.g. "DHAN"
    exchange_ts: datetime  # as stamped by the exchange/broker (often whole seconds)
    received_ts: datetime  # when the platform received it
    bids: tuple[Level, ...] = ()  # best (highest) first
    asks: tuple[Level, ...] = ()  # best (lowest) first
    last_price: float | None = None
    last_quantity: float | None = None
    volume: float | None = None  # cumulative for the day
    open_interest: float | None = None
    total_buy_quantity: float | None = None  # all pending buy orders (exchange total, not just visible)
    total_sell_quantity: float | None = None
    capacity: int = 5  # how many levels this source can show (5, 20, 200)
    meta: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def best_bid(self) -> Level | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Level | None:
        return self.asks[0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks and self.bids[0].price < self.asks[0].price:
            return (self.bids[0].price + self.asks[0].price) / 2
        return None

    @property
    def spread(self) -> float | None:
        if self.bids and self.asks:
            return self.asks[0].price - self.bids[0].price
        return None

    def is_valid(self) -> bool:
        """At least one side, sorted, positive, and not crossed."""
        if not self.bids and not self.asks:
            return self.last_price is not None
        if any(lv.price <= 0 or lv.quantity < 0 for lv in (*self.bids, *self.asks)):
            return False
        pairs = zip(self.bids[1:], self.bids, strict=False)
        if any(deeper.price >= better.price for deeper, better in pairs):
            return False
        pairs = zip(self.asks[1:], self.asks, strict=False)
        if any(deeper.price <= better.price for deeper, better in pairs):
            return False
        return not (self.bids and self.asks and self.bids[0].price >= self.asks[0].price)

    def quote(self) -> Quote | None:
        """Top of book for the trading cache (which works in Decimal)."""
        if not self.bids or not self.asks or self.bids[0].price >= self.asks[0].price:
            return None
        bid, ask = self.bids[0], self.asks[0]
        return Quote(
            self.instrument_id,
            self.exchange_ts,
            _dec(bid.price),
            _dec(bid.quantity),
            _dec(ask.price),
            _dec(ask.quantity),
        )

    def depth_quantity(self, side: str, levels: int | None = None) -> float:
        rows = self.bids if side == BUY else self.asks
        return sum(lv.quantity for lv in (rows[:levels] if levels else rows))


@dataclass(frozen=True)
class Tick:
    """Traded quantity between two observations, with the side that initiated it (estimated)."""

    instrument_id: str
    source: str
    at: datetime
    price: float
    quantity: float
    side: str  # BUY (hit the ask), SELL (hit the bid) or "" when it cannot be told
    volume: float | None = None  # cumulative day volume after this tick
    inferred: bool = True
    received_ts: datetime | None = None


def _dec(value: float) -> Decimal:
    return Decimal(repr(round(value, 8)))


def classify(price: float, previous: BookSnapshot | None, last_price: float | None) -> str:
    """Quote rule against the book before the trade, then the tick rule."""
    if previous is not None:
        bid, ask = previous.best_bid, previous.best_ask
        if ask is not None and price >= ask.price:
            return BUY
        if bid is not None and price <= bid.price:
            return SELL
        if bid is not None and ask is not None:
            mid = (bid.price + ask.price) / 2
            if price > mid:
                return BUY
            if price < mid:
                return SELL
    if last_price is not None:
        if price > last_price:
            return BUY
        if price < last_price:
            return SELL
    return ""


def infer_tick(previous: BookSnapshot | None, current: BookSnapshot) -> Tick | None:
    """The trading between two snapshots of one source, from the change in cumulative volume.

    Returns None when nothing traded, or when the volume went backwards (a new session or a reset feed).
    Without a cumulative volume, a changed last-trade quantity or price counts as one trade.
    """
    price = current.last_price
    if price is None or price <= 0:
        return None
    last_price = previous.last_price if previous is not None else None
    if current.volume is not None and previous is not None and previous.volume is not None:
        traded = current.volume - previous.volume
        if traded <= 0:
            return None
    elif previous is None:
        return None
    else:
        changed = (current.last_quantity, current.last_price, current.exchange_ts) != (
            previous.last_quantity,
            previous.last_price,
            previous.exchange_ts,
        )
        if not changed or not current.last_quantity:
            return None
        traded = current.last_quantity
    return Tick(
        current.instrument_id,
        current.source,
        current.exchange_ts,
        price,
        traded,
        classify(price, previous, last_price),
        current.volume,
        received_ts=current.received_ts,
    )


def snapshot_from_quote(quote: Quote, source: str, received_ts: datetime) -> BookSnapshot:
    """A one-level book from a plain quote, for sources that only publish the top of book."""
    return BookSnapshot(
        quote.instrument_id,
        source,
        quote.exchange_ts,
        received_ts,
        bids=(Level(float(quote.bid_price), float(quote.bid_size)),),
        asks=(Level(float(quote.ask_price), float(quote.ask_size)),),
        capacity=1,
    )


def levels(
    rows, *, bid: bool, price_key="price", qty_key="quantity", orders_key="orders", scale: float = 1.0
) -> tuple[Level, ...]:
    """One side of the book from broker rows: empty slots dropped, best price first."""
    out = []
    for row in rows or ():
        try:
            price = float(row.get(price_key) or 0) / scale
            qty = float(row.get(qty_key) or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if price <= 0 or qty <= 0:
            continue
        orders = row.get(orders_key)
        out.append(Level(price, qty, int(orders) if orders not in (None, "") else None))
    return sort_side(out, bid=bid)


def sort_side(rows, *, bid: bool) -> tuple[Level, ...]:
    """Best price first, merging repeated prices."""
    merged: dict[float, Level] = {}
    for lv in rows:
        if lv.price <= 0 or lv.quantity <= 0:
            continue
        seen = merged.get(lv.price)
        if seen is None:
            merged[lv.price] = lv
        else:
            orders = None if seen.orders is None or lv.orders is None else seen.orders + lv.orders
            merged[lv.price] = Level(lv.price, seen.quantity + lv.quantity, orders)
    return tuple(sorted(merged.values(), key=lambda lv: -lv.price if bid else lv.price))
