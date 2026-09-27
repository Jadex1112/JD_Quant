"""Indian market costs (NSE cash, MCX, currency futures), crypto and forex routing, and NSE hours.

Statutory rates are those in force for NSE equity as of 2025 and change from time to time; brokerage
defaults follow Fyers' published plan. Every rate is a field so it can be matched to a contract note.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from jdquant.core.types import Side
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.instruments import AssetClass, Instrument
from jdquant.markets.sessions import FX_VENUES, MarketSession
from jdquant.markets.sessions import IST as IST  # re-exported for existing callers
from jdquant.oms.orders import Liquidity

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
class IndiaDerivativeFees(FeeSchedule):
    """Charges on one executed MCX commodity or NSE currency futures order, in rupees (approximate)."""

    brokerage_rate: Decimal = Decimal("0.0003")
    brokerage_cap: Decimal = Decimal(20)
    ctt_sell: Decimal = Decimal("0.0001")  # commodity transaction tax, non-agri futures, sell side
    exchange_txn: Decimal = Decimal("0.000021")
    sebi_fee: Decimal = Decimal("0.000001")
    stamp_buy: Decimal = Decimal("0.00002")
    gst: Decimal = Decimal("0.18")

    def fee(
        self, instrument: Instrument, side: Side, quantity: Decimal, price: Decimal, liquidity: Liquidity
    ) -> Decimal:
        return self.breakdown(side, instrument.notional(quantity, price))["total"]

    def breakdown(self, side: Side, turnover: Decimal) -> dict[str, Decimal]:
        brokerage = min(turnover * self.brokerage_rate, self.brokerage_cap)
        exchange, sebi = turnover * self.exchange_txn, turnover * self.sebi_fee
        parts = {
            "brokerage": brokerage,
            "ctt": turnover * self.ctt_sell if side is Side.SELL else Decimal(0),
            "exchange": exchange,
            "sebi": sebi,
            "stamp": turnover * self.stamp_buy if side is Side.BUY else Decimal(0),
            "gst": (brokerage + exchange + sebi) * self.gst,
        }
        parts = {k: v.quantize(PAISA, ROUND_HALF_UP) for k, v in parts.items()}
        parts["total"] = sum(parts.values(), Decimal(0))
        return parts

    def fingerprint(self) -> list[str]:
        return [type(self).__name__, str(self.brokerage_rate), str(self.ctt_sell), str(self.exchange_txn)]


MCX_FEES = IndiaDerivativeFees()
CURRENCY_FEES = IndiaDerivativeFees(
    ctt_sell=Decimal(0), exchange_txn=Decimal("0.0000035"), stamp_buy=Decimal("0.000001")
)


@dataclass(frozen=True)
class CryptoExchangeFees(FeeSchedule):
    """Spot crypto: exchange fee plus 18% GST on that fee, as charged to Indian users (approximate).

    The 1% TDS deducted on crypto sales in India is a tax prepayment credited against the tax bill, not a
    charge, so it is not modelled here; it does tie up cash until the return is filed.
    """

    rate: Decimal = Decimal("0.001")  # Binance spot taker fee without discounts
    gst: Decimal = Decimal("0.18")

    def fee(
        self, instrument: Instrument, side: Side, quantity: Decimal, price: Decimal, liquidity: Liquidity
    ) -> Decimal:
        return instrument.notional(quantity, price) * self.rate * (1 + self.gst)

    def fingerprint(self) -> list[str]:
        return [type(self).__name__, str(self.rate), str(self.gst)]


CRYPTO_FEES = CryptoExchangeFees()


def fees_for(instrument: Instrument, product: Product = Product.CNC) -> FeeSchedule:
    """The charges model for an instrument's market (flat 10 bps where no local model exists)."""
    if instrument.venue == "MCX":
        return MCX_FEES
    if instrument.venue == "NSE" and instrument.asset_class is AssetClass.FX:
        return CURRENCY_FEES
    if instrument.venue == "NSE":
        return IndiaEquityFees(product=product)
    if instrument.venue == "BINANCE":
        return CRYPTO_FEES
    if instrument.venue in FX_VENUES:
        from jdquant.markets.forex import FX_FEES

        return FX_FEES
    return FeeSchedule()


@dataclass(frozen=True)
class MarketFees(FeeSchedule):
    """Routes every fill to its market's charges model (used by the paper venue)."""

    product: Product = Product.CNC

    def fee(
        self, instrument: Instrument, side: Side, quantity: Decimal, price: Decimal, liquidity: Liquidity
    ) -> Decimal:
        return fees_for(instrument, self.product).fee(instrument, side, quantity, price, liquidity)

    def financing_rate(self, instrument: Instrument, direction: int) -> Decimal:
        return fees_for(instrument, self.product).financing_rate(instrument, direction)

    def fingerprint(self) -> list[str]:
        return [type(self).__name__, self.product.value]


@dataclass(frozen=True)
class NseCalendar(MarketSession):
    """NSE cash-market session (kept for callers that want India's equity hours explicitly)."""
