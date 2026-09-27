"""Indian cash-equity costs and NSE trading hours.

Statutory rates are those in force for NSE equity as of 2025 and change from time to time; brokerage
defaults follow Fyers' published plan. Every rate is a field so it can be matched to a contract note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from jdquant.core.types import Side
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.instruments import Instrument
from jdquant.oms.orders import Liquidity

IST = timezone(timedelta(hours=5, minutes=30), "IST")
PAISA = Decimal("0.01")


class Product(StrEnum):
    """Fyers product types used by the platform."""

    CNC = "CNC"  # delivery: positions may be held overnight
    INTRADAY = "INTRADAY"  # squared off the same day


@dataclass(frozen=True)
class IndiaEquityFees(FeeSchedule):
    """Charges on one executed NSE equity order, in rupees."""

    product: Product = Product.CNC
    brokerage_rate: Decimal = Decimal("0.0003")  # 0.03% ...
    brokerage_cap: Decimal = Decimal(20)  # ... or ₹20 per executed order, whichever is lower
    delivery_brokerage_rate: Decimal = Decimal("0.003")  # delivery: 0.3% or ₹20, whichever is lower
    stt_delivery: Decimal = Decimal("0.001")  # both sides
    stt_intraday_sell: Decimal = Decimal("0.00025")  # sell side only
    exchange_txn: Decimal = Decimal("0.0000297")  # NSE transaction charge
    sebi_fee: Decimal = Decimal("0.000001")  # ₹10 per crore
    stamp_delivery_buy: Decimal = Decimal("0.00015")
    stamp_intraday_buy: Decimal = Decimal("0.00003")
    gst: Decimal = Decimal("0.18")  # on brokerage + exchange + SEBI charges
    dp_charge: Decimal = Decimal(15)  # per delivery sell, depository charge incl. GST (approximate)

    def fee(
        self, instrument: Instrument, side: Side, quantity: Decimal, price: Decimal, liquidity: Liquidity
    ) -> Decimal:
        return self.breakdown(side, instrument.notional(quantity, price))["total"]

    def breakdown(self, side: Side, turnover: Decimal) -> dict[str, Decimal]:
        delivery = self.product is Product.CNC
        rate = self.delivery_brokerage_rate if delivery else self.brokerage_rate
        brokerage = min(turnover * rate, self.brokerage_cap)
        if delivery:
            stt = turnover * self.stt_delivery
        else:
            stt = turnover * self.stt_intraday_sell if side is Side.SELL else Decimal(0)
        exchange = turnover * self.exchange_txn
        sebi = turnover * self.sebi_fee
        stamp = turnover * (self.stamp_delivery_buy if delivery else self.stamp_intraday_buy)
        stamp = stamp if side is Side.BUY else Decimal(0)
        gst = (brokerage + exchange + sebi) * self.gst
        dp = self.dp_charge if delivery and side is Side.SELL else Decimal(0)
        parts = {
            "brokerage": brokerage,
            "stt": stt,
            "exchange": exchange,
            "sebi": sebi,
            "stamp": stamp,
            "gst": gst,
            "dp": dp,
        }
        parts = {k: v.quantize(PAISA, ROUND_HALF_UP) for k, v in parts.items()}
        parts["total"] = sum(parts.values(), Decimal(0))
        return parts

    def fingerprint(self) -> list[str]:
        return [type(self).__name__, self.product.value, str(self.brokerage_rate), str(self.stt_delivery)]


@dataclass(frozen=True)
class NseCalendar:
    """Regular NSE cash-market session in IST. Holidays are supplied by configuration or the broker."""

    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    intraday_cutoff: time = time(15, 10)  # exit intraday positions before the broker's auto square-off
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def is_open(self, at: datetime) -> bool:
        local = at.astimezone(IST)
        return self.is_trading_day(local.date()) and self.open_time <= local.time() < self.close_time

    def past_intraday_cutoff(self, at: datetime) -> bool:
        local = at.astimezone(IST)
        return local.time() >= self.intraday_cutoff

    def next_open(self, at: datetime) -> datetime:
        local = at.astimezone(IST)
        day = local.date()
        if local.time() >= self.open_time:
            day += timedelta(days=1)
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return datetime.combine(day, self.open_time, IST)

    def session_close(self, day: date) -> datetime:
        return datetime.combine(day, self.close_time, IST)
