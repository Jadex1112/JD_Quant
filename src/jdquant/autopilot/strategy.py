"""The single strategy the autopilot deploys.

Research, paper trading and live trading all run this class with the same parameters, so what was
backtested is exactly what trades. It is long-only (Indian cash equities cannot be shorted overnight),
sizes each entry from its allocated capital, and applies a protective stop.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from jdquant.core.errors import PlatformError
from jdquant.marketdata.records import Candle
from jdquant.markets.india import NseCalendar
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.indicators import bollinger, donchian, rsi, sma

SIGNALS = ("ma_cross", "rsi", "bollinger", "donchian", "ml")
CALENDAR = NseCalendar()


class AutopilotStrategy(Strategy):
    name = "autopilot"
    version = "1.0.0"
    description = "AI-selected long-only signal with capital-based sizing and a protective stop."
    parameters = {
        "signal": Param(str, "ma_cross", description=f"one of {', '.join(SIGNALS)}"),
        "fast": Param(int, 10, min=2, max=500, description="fast moving average (ma_cross)"),
        "slow": Param(int, 30, min=3, max=500, description="slow moving average (ma_cross)"),
        "period": Param(int, 14, min=2, max=500, description="lookback (rsi, bollinger, donchian)"),
        "lower": Param(Decimal, "30", min=Decimal(1), max=Decimal(99), description="RSI entry level"),
        "upper": Param(Decimal, "70", min=Decimal(1), max=Decimal(99), description="RSI exit level"),
        "k": Param(Decimal, "2", min=Decimal("0.5"), max=Decimal(5), description="Bollinger width"),
        "model": Param(str, "", description="registered model name (ml)"),
        "threshold": Param(Decimal, "0.05", min=Decimal(0), max=Decimal("0.49"), description="ml band"),
        "capital": Param(Decimal, "100000", min=Decimal(0), description="capital allocated, quote currency"),
        "stop_loss": Param(Decimal, "0.08", min=Decimal(0), max=Decimal("0.5"), description="0 disables"),
        "take_profit": Param(Decimal, "0", min=Decimal(0), max=Decimal(5), description="0 disables"),
        "intraday": Param(bool, False, description="exit before the NSE intraday cutoff; no late entries"),
        "trade_after": Param(str, "", description="ISO time; bars closing earlier only warm up"),
    }

    def on_init(self) -> None:
        p = self.ctx.params
        if p["signal"] not in SIGNALS:
            raise PlatformError("PARAMETER_INVALID", f"signal must be one of {SIGNALS}")
        self._trade_after = datetime.fromisoformat(p["trade_after"]) if p["trade_after"] else None

    def on_bar(self, candle: Candle) -> None:
        if self._trade_after is not None and candle.close_ts < self._trade_after:
            return
        instrument_id = candle.instrument_id
        if self.ctx.open_orders(instrument_id):
            return
        p = self.ctx.params
        position = self.ctx.position(instrument_id)
        late = p["intraday"] and CALENDAR.past_intraday_cutoff(candle.close_ts)
        if position > 0:
            if late or self._stopped_out(instrument_id, candle.close) or self.decide(candle) == "exit":
                self.ctx.sell(instrument_id, position)
            return
        if not late and self.decide(candle) == "enter":
            quantity = self._size(instrument_id, candle.close)
            if quantity > 0:
                self.ctx.buy(instrument_id, quantity)

    def decide(self, candle: Candle) -> str | None:
        """'enter', 'exit' or None (no view) from the configured signal."""
        p, i = self.ctx.params, candle.instrument_id
        close = float(candle.close)
        match p["signal"]:
            case "ma_cross":
                closes = self.ctx.closes(i, p["slow"])
                fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
                if fast is None or slow is None:
                    return None
                return "enter" if fast > slow else "exit"
            case "rsi":
                value = rsi(self.ctx.closes(i, p["period"] * 5), p["period"])
                if value is None:
                    return None
                return "enter" if value < float(p["lower"]) else "exit" if value > float(p["upper"]) else None
            case "bollinger":
                bands = bollinger(self.ctx.closes(i, p["period"]), p["period"], float(p["k"]))
                if bands is None:
                    return None
                lower, middle, _ = bands
                return "enter" if close < lower else "exit" if close >= middle else None
            case "donchian":
                prior = self.ctx.candles(i, p["period"] + 1)[:-1]
                channel = donchian([c.high for c in prior], [c.low for c in prior], p["period"])
                if channel is None:
                    return None
                low, high = channel
                return "enter" if close > high else "exit" if close < low else None
            case "ml":
                try:
                    probability = self.ctx.predict(p["model"], i)
                except PlatformError as exc:
                    if exc.code == "INPUT_INVALID":
                        return None  # still warming up
                    raise
                band = float(p["threshold"])
                return "enter" if probability > 0.5 + band else "exit" if probability < 0.5 - band else None
        return None

    def _stopped_out(self, instrument_id: str, price: Decimal) -> bool:
        p = self.ctx.params
        entry = self.ctx.entry_price(instrument_id)
        if entry <= 0:
            return False
        if p["stop_loss"] > 0 and price <= entry * (1 - p["stop_loss"]):
            return True
        return p["take_profit"] > 0 and price >= entry * (1 + p["take_profit"])

    def _size(self, instrument_id: str, price: Decimal) -> Decimal:
        instrument = self.ctx.instrument(instrument_id)
        lots = self.ctx.params["capital"] / (price * instrument.contract_multiplier) / instrument.lot_size
        quantity = lots.to_integral_value(rounding=ROUND_FLOOR) * instrument.lot_size
        return quantity if quantity >= instrument.min_quantity else Decimal(0)
