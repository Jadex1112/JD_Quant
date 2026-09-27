"""Deterministic simulated venue for paper trading and backtesting (FR-22080 – FR-22086)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from jdquant.core.clock import Clock
from jdquant.core.types import ZERO, Side
from jdquant.marketdata.cache import MarketDataCache
from jdquant.marketdata.instruments import Instrument, InstrumentRegistry
from jdquant.marketdata.records import Candle, Trade
from jdquant.oms.orders import ExecutionReport, Liquidity, Order, OrderType, ReportType, TimeInForce

BPS = Decimal(10_000)


class FillTiming(StrEnum):
    IMMEDIATE = "IMMEDIATE"  # marketable orders fill at the current reference price (paper trading)
    NEXT_BAR = "NEXT_BAR"  # market orders fill at the next bar's open (bar backtests, BR-25-03)


@dataclass(frozen=True)
class FeeSchedule:
    maker_bps: Decimal = Decimal(10)
    taker_bps: Decimal = Decimal(10)


class SimulatedVenue:
    def __init__(
        self,
        venue: str,
        clock: Clock,
        instruments: InstrumentRegistry,
        market: MarketDataCache,
        *,
        fill_timing: FillTiming = FillTiming.IMMEDIATE,
        fees: FeeSchedule | None = None,
        slippage_bps: Decimal = ZERO,
        session_id: str | None = None,
    ):
        self.venue = venue
        self._clock = clock
        self._instruments = instruments
        self._market = market
        self.fill_timing = fill_timing
        self.fees = fees or FeeSchedule()
        self.slippage_bps = slippage_bps
        self._handler: Callable[[ExecutionReport], None] | None = None
        self._resting: dict[str, Order] = {}
        self._pending_market: dict[str, Order] = {}
        self._known: set[str] = set()
        self._trade_seq = 0
        # Trade ids must stay unique across restarts, because fills are de-duplicated by them.
        self._session = session_id or uuid.uuid4().hex[:12]

    def set_report_handler(self, handler: Callable[[ExecutionReport], None]) -> None:
        self._handler = handler

    # ---- ExecutionRouter --------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        instrument = self._instruments.get(order.instrument_id)
        reason = self._venue_validation(order, instrument)
        if reason:
            self._emit(ReportType.REJECT, order, reason=reason)
            return
        self._known.add(order.client_order_id)
        self._emit(ReportType.ACK, order, venue_order_id=f"SIMV-{order.client_order_id}")

        if order.order_type is OrderType.MARKET:
            if self.fill_timing is FillTiming.NEXT_BAR:
                self._pending_market[order.client_order_id] = order
            else:
                reference = self._market.reference_price(order.instrument_id)
                if reference is None:
                    self._emit(ReportType.EXPIRED, order, reason="NO_LIQUIDITY")
                else:
                    self._fill(order, self._slipped(reference, order.side, instrument), Liquidity.TAKER)
            return

        reference = self._market.reference_price(order.instrument_id)
        marketable = reference is not None and self._crosses(order, reference)
        if marketable and self.fill_timing is FillTiming.IMMEDIATE:
            self._fill(order, reference, Liquidity.TAKER)
        elif order.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
            self._emit(ReportType.EXPIRED, order, reason="IOC_NOT_FILLED")
        else:
            self._resting[order.client_order_id] = order

    def cancel(self, order: Order) -> None:
        removed = self._resting.pop(order.client_order_id, None) or self._pending_market.pop(
            order.client_order_id, None
        )
        if removed is None:
            self._emit(ReportType.CANCEL_REJECT, order, reason="ORDER_NOT_WORKING")
        else:
            self._emit(ReportType.CANCELED, order)

    def restore_working(self, order: Order) -> None:
        """Rebuild the simulated book from persisted working orders after a restart."""
        self._known.add(order.client_order_id)
        if order.order_type is OrderType.LIMIT:
            self._resting[order.client_order_id] = order
        elif order.order_type is OrderType.MARKET and self.fill_timing is FillTiming.NEXT_BAR:
            self._pending_market[order.client_order_id] = order

    def query(self, order: Order) -> None:
        if order.client_order_id not in self._known:
            self._emit(ReportType.NOT_FOUND, order)

    # ---- market events ----------------------------------------------------------------------

    def on_bar(self, candle: Candle) -> None:
        for order in [o for o in self._pending_market.values() if o.instrument_id == candle.instrument_id]:
            del self._pending_market[order.client_order_id]
            instrument = self._instruments.get(order.instrument_id)
            self._fill(order, self._slipped(candle.open, order.side, instrument), Liquidity.TAKER)
        for order in [o for o in self._resting.values() if o.instrument_id == candle.instrument_id]:
            assert order.limit_price is not None
            # Limit orders fill only when price trades strictly through the limit (BR-25-02).
            touched = (
                candle.low < order.limit_price if order.side is Side.BUY else candle.high > order.limit_price
            )
            if touched:
                del self._resting[order.client_order_id]
                price = (
                    min(order.limit_price, candle.open)
                    if order.side is Side.BUY
                    else max(order.limit_price, candle.open)
                )
                self._fill(order, price, Liquidity.MAKER)

    def on_trade(self, trade: Trade) -> None:
        for order in [o for o in self._resting.values() if o.instrument_id == trade.instrument_id]:
            assert order.limit_price is not None
            through = (
                trade.price < order.limit_price if order.side is Side.BUY else trade.price > order.limit_price
            )
            if through:
                del self._resting[order.client_order_id]
                self._fill(order, order.limit_price, Liquidity.MAKER)

    # ---- internals --------------------------------------------------------------------------

    def _venue_validation(self, order: Order, instrument: Instrument) -> str | None:
        if not instrument.is_quantity_aligned(order.quantity):
            return "INVALID_QUANTITY"
        if order.limit_price is not None and not instrument.is_price_aligned(order.limit_price):
            return "PRICE_OUT_OF_BAND"
        if order.post_only:
            reference = self._market.reference_price(order.instrument_id)
            if reference is not None and self._crosses(order, reference):
                return "POST_ONLY_WOULD_TAKE"
        return None

    @staticmethod
    def _crosses(order: Order, reference: Decimal) -> bool:
        assert order.limit_price is not None
        return order.limit_price >= reference if order.side is Side.BUY else order.limit_price <= reference

    def _slipped(self, price: Decimal, side: Side, instrument: Instrument) -> Decimal:
        if not self.slippage_bps:
            return price
        adjusted = price * (1 + side.sign * self.slippage_bps / BPS)
        rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
        return (adjusted / instrument.tick_size).to_integral_value(rounding=rounding) * instrument.tick_size

    def _fill(self, order: Order, price: Decimal, liquidity: Liquidity) -> None:
        instrument = self._instruments.get(order.instrument_id)
        quantity = order.remaining_quantity
        bps = self.fees.maker_bps if liquidity is Liquidity.MAKER else self.fees.taker_bps
        fee = instrument.notional(quantity, price) * bps / BPS
        self._trade_seq += 1
        self._emit(
            ReportType.FILL,
            order,
            price=price,
            quantity=quantity,
            fee=fee,
            fee_asset=instrument.quote_asset,
            liquidity=liquidity,
            venue_trade_id=f"SIMT-{self._session}-{self._trade_seq:010d}",
        )

    def _emit(self, report_type: ReportType, order: Order, **fields) -> None:
        if self._handler is None:
            raise RuntimeError("simulated venue has no report handler")
        self._handler(
            ExecutionReport(
                report_type=report_type,
                venue=self.venue,
                client_order_id=order.client_order_id,
                exchange_ts=self._clock.now(),
                is_simulated=True,
                **fields,
            )
        )
