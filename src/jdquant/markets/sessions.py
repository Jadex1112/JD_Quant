"""Trading sessions per market: when each instrument trades, and how many bars make a year."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo

from jdquant.marketdata.instruments import AssetClass, Instrument

IST = timezone(timedelta(hours=5, minutes=30), "IST")
CRYPTO = (AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL, AssetClass.CRYPTO_FUTURE)
GOLD_TOKENS = ("PAXG", "XAUT")  # one token = one troy ounce of gold, priced in dollars: an XAU/USD proxy


@dataclass(frozen=True)
class MarketSession:
    name: str = "NSE equity"
    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    intraday_cutoff: time = time(15, 10)  # exit intraday positions before the broker squares them off
    holidays: frozenset[date] = field(default_factory=frozenset)
    tz: tzinfo = IST
    always_open: bool = False  # crypto trades around the clock, every day
    trading_days_per_year: int = 248

    @property
    def seconds_per_day(self) -> int:
        if self.always_open:
            return 86400
        start = datetime.combine(date(2000, 1, 3), self.open_time)
        return int((datetime.combine(date(2000, 1, 3), self.close_time) - start).total_seconds())

    def bars_per_year(self, interval_seconds: int) -> float:
        if interval_seconds >= 86400:
            return self.trading_days_per_year * 86400 / interval_seconds
        return self.trading_days_per_year * max(1, self.seconds_per_day // interval_seconds)

    def is_trading_day(self, day: date) -> bool:
        return self.always_open or (day.weekday() < 5 and day not in self.holidays)

    def is_open(self, at: datetime) -> bool:
        if self.always_open:
            return True
        local = at.astimezone(self.tz)
        return self.is_trading_day(local.date()) and self.open_time <= local.time() < self.close_time

    def past_intraday_cutoff(self, at: datetime) -> bool:
        if self.always_open:
            return False
        return at.astimezone(self.tz).time() >= self.intraday_cutoff

    def next_open(self, at: datetime) -> datetime:
        if self.always_open:
            return at
        local = at.astimezone(self.tz)
        day = local.date()
        if local.time() >= self.open_time:
            day += timedelta(days=1)
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return datetime.combine(day, self.open_time, self.tz)

    def session_close(self, day: date) -> datetime:
        return datetime.combine(day, self.close_time, self.tz)


NSE_EQUITY = MarketSession()
# MCX runs into the night; it closes at 23:30 IST while the US is on daylight time and 23:55 otherwise.
# The earlier close is used so intraday exits are always inside the session.
MCX = MarketSession("MCX commodities", time(9, 0), time(23, 30), time(23, 0))
NSE_CURRENCY = MarketSession("NSE currency", time(9, 0), time(17, 0), time(16, 45))
CRYPTO_24_7 = MarketSession("Crypto 24/7", time(0, 0), time(23, 59, 59), time(23, 59, 59), tz=UTC,
                            always_open=True, trading_days_per_year=365)  # fmt: skip


def session_for(instrument: Instrument) -> MarketSession:
    if instrument.asset_class in CRYPTO or instrument.venue in ("BINANCE",):
        return CRYPTO_24_7
    if instrument.venue == "MCX":
        return MCX
    if instrument.venue == "NSE" and instrument.asset_class is AssetClass.FX:
        return NSE_CURRENCY
    if instrument.venue == "NSE":
        return NSE_EQUITY
    # US equities and anything else: treat as a regular weekday market, UTC day boundaries.
    return MarketSession("Regular", time(0, 0), time(23, 59, 59), time(23, 59, 59), tz=UTC,
                         trading_days_per_year=252)  # fmt: skip


def asset_group(instrument: Instrument) -> str:
    """Human grouping used by the autopilot and the UI."""
    if instrument.asset_class in CRYPTO and instrument.base_asset in GOLD_TOKENS:
        return "Gold · XAU/USD (tokenized)"
    if instrument.asset_class in CRYPTO:
        return "Crypto"
    if instrument.asset_class is AssetClass.FX:
        return "Currency"
    if instrument.venue == "MCX" or instrument.asset_class is AssetClass.COMMODITY:
        return "Commodities"
    if instrument.asset_class is AssetClass.ETF:
        return "ETFs (incl. gold & silver)"
    return "Stocks"
