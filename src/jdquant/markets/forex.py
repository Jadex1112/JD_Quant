"""Costs of trading forex and spot metals with a market maker such as OANDA.

There is no commission on a standard account. The cost is the spread (you buy at the ask and sell at the
bid) and financing: a position still open at the 17:00 New York rollover pays (or earns) the interest-rate
difference between the two currencies plus the broker's markup, three days' worth on Wednesdays to cover
the weekend.

Fills in backtests and paper trading are priced at the mid, so half the typical spread is charged on each
fill. Live fills already include the spread in their price and carry no extra fee. The spreads and rates
below are approximations for a standard account; edit them to match your broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from jdquant.core.types import Side
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.instruments import Instrument
from jdquant.markets.sessions import NEW_YORK
from jdquant.oms.orders import Liquidity

ROLLOVER = time(17, 0)

# Typical spread in price units (e.g. 0.00014 = 1.4 pips on EUR/USD, 0.40 = 40 cents on gold).
TYPICAL_SPREAD: dict[str, Decimal] = {
    "EUR_USD": Decimal("0.00014"),
    "GBP_USD": Decimal("0.00020"),
    "USD_JPY": Decimal("0.014"),
    "AUD_USD": Decimal("0.00014"),
    "NZD_USD": Decimal("0.00020"),
    "USD_CAD": Decimal("0.00022"),
    "USD_CHF": Decimal("0.00018"),
    "EUR_GBP": Decimal("0.00018"),
    "EUR_JPY": Decimal("0.020"),
    "GBP_JPY": Decimal("0.030"),
    "XAU_USD": Decimal("0.40"),
    "XAG_USD": Decimal("0.025"),
}
DEFAULT_SPREAD_BPS = Decimal(3)  # for pairs not listed: 3 basis points of the price

# Approximate annual interest rates per currency (policy rates; metals earn close to nothing).
INTEREST: dict[str, Decimal] = {
    "USD": Decimal("0.040"),
    "EUR": Decimal("0.020"),
    "GBP": Decimal("0.040"),
    "JPY": Decimal("0.005"),
    "CHF": Decimal("0.000"),
    "AUD": Decimal("0.036"),
    "NZD": Decimal("0.030"),
    "CAD": Decimal("0.0275"),
    "XAU": Decimal("0.000"),
    "XAG": Decimal("0.000"),
}
FINANCING_MARKUP = Decimal("0.025")  # the broker's charge on top of the rate difference, per year


def half_spread(instrument: Instrument, price: Decimal) -> Decimal:
    spread = TYPICAL_SPREAD.get(instrument.symbol)
    if spread is None:
        spread = price * DEFAULT_SPREAD_BPS / Decimal(10_000)
    return spread / 2


def financing_rate(instrument: Instrument, direction: int) -> Decimal:
    """Annual cost of holding a long (+1) or short (-1) position overnight, as a fraction of notional.

    Long BASE/QUOTE earns the base rate and pays the quote rate; short is the reverse. Credits are not
    counted (the rate never goes below zero), so the model errs on the side of cost.
    """
    base = INTEREST.get(instrument.base_asset, Decimal(0))
    quote = INTEREST.get(instrument.quote_asset, Decimal(0))
    difference = (quote - base) if direction > 0 else (base - quote)
    return max(Decimal(0), difference + FINANCING_MARKUP)


def rollover_days(start: datetime, end: datetime) -> int:
    """Days of financing charged for holding a position from `start` to `end`.

    One day per weekday rollover (17:00 New York) in (start, end]; Wednesday's counts three.
    """
    if end <= start:
        return 0
    local_start, local_end = start.astimezone(NEW_YORK), end.astimezone(NEW_YORK)
    days, day = 0, local_start.date()
    while day <= local_end.date():
        instant = datetime.combine(day, ROLLOVER, NEW_YORK)
        if local_start < instant <= local_end and day.weekday() < 5:
            days += 3 if day.weekday() == 2 else 1
        day += timedelta(days=1)
    return days


@dataclass(frozen=True)
class FxSpreadFees(FeeSchedule):
    """Half the typical spread on every fill priced at the mid, plus overnight financing."""

    def fee(
        self, instrument: Instrument, side: Side, quantity: Decimal, price: Decimal, liquidity: Liquidity
    ) -> Decimal:
        return quantity * instrument.contract_multiplier * half_spread(instrument, price)

    def financing_rate(self, instrument: Instrument, direction: int) -> Decimal:
        return financing_rate(instrument, direction)

    def fingerprint(self) -> list[str]:
        return [type(self).__name__, str(FINANCING_MARKUP)]


FX_FEES = FxSpreadFees()
