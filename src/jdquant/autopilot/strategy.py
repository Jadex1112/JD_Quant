"""The single strategy the autopilot deploys.

Research, paper trading and live trading all run this class with the same parameters, so what was
backtested is exactly what trades. It is long-only (Indian cash equities cannot be shorted overnight),
sizes each entry from its allocated capital, and applies a protective stop (fixed and optionally trailing).
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from jdquant.core.errors import PlatformError
from jdquant.marketdata.records import Candle
from jdquant.markets.india import IST, NseCalendar
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.indicators import (
    bollinger,
    donchian,
    macd,
    rate_of_change,
    rsi,
    sma,
    supertrend,
    zscore,
)

SIGNALS = (
    "ma_cross", "rsi", "bollinger", "donchian", "ml",
    "momentum", "macd", "supertrend", "rsi_trend", "volume_breakout", "orb",
)  # fmt: skip
INTRADAY_ONLY = ("orb",)
CALENDAR = NseCalendar()


class AutopilotStrategy(Strategy):
    name = "autopilot"
    version = "1.1.0"
    description = "AI-selected long-only signal with capital-based sizing and a protective stop."
    parameters = {
        "signal": Param(str, "ma_cross", description=f"one of {', '.join(SIGNALS)}"),
        "fast": Param(int, 10, min=2, max=500, description="fast moving average (ma_cross)"),
        "slow": Param(int, 30, min=3, max=500, description="slow moving average (ma_cross)"),
        "period": Param(
            int, 14, min=2, max=500, description="lookback (rsi, bollinger, donchian, momentum, ...)"
        ),
        "trend": Param(int, 200, min=2, max=500, description="trend-filter moving average (rsi_trend)"),
        "signal_len": Param(int, 9, min=2, max=100, description="MACD signal-line length"),
        "lower": Param(Decimal, "30", min=Decimal(1), max=Decimal(99), description="RSI entry level"),
        "upper": Param(Decimal, "70", min=Decimal(1), max=Decimal(99), description="RSI exit level"),
        "k": Param(
            Decimal,
            "2",
            min=Decimal("0.5"),
            max=Decimal(5),
            description="Bollinger width, Supertrend ATR multiple, or volume z-score (volume_breakout)",
        ),  # fmt: skip
        "model": Param(str, "", description="registered model name (ml)"),
        "threshold": Param(Decimal, "0.05", min=Decimal(0), max=Decimal("0.49"), description="ml band"),
        "capital": Param(Decimal, "100000", min=Decimal(0), description="capital allocated, quote currency"),
        "stop_loss": Param(Decimal, "0.08", min=Decimal(0), max=Decimal("0.5"), description="0 disables"),
        "take_profit": Param(Decimal, "0", min=Decimal(0), max=Decimal(5), description="0 disables"),
        "trailing_stop": Param(
            Decimal,
            "0",
            min=Decimal(0),
            max=Decimal("0.5"),
            description="exit this far below the high since entry",
        ),
        "intraday": Param(bool, False, description="exit before the NSE intraday cutoff; no late entries"),
        "trade_after": Param(str, "", description="ISO time; bars closing earlier only warm up"),
    }

    def on_init(self) -> None:
        p = self.ctx.params
        if p["signal"] not in SIGNALS:
            raise PlatformError("PARAMETER_INVALID", f"signal must be one of {SIGNALS}")
        self._trade_after = datetime.fromisoformat(p["trade_after"]) if p["trade_after"] else None
        self._peaks: dict[str, Decimal] = {}  # highest close since entry, for the trailing stop

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
        self._peaks.pop(instrument_id, None)
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
            case "momentum":
                # Time-series momentum: hold while the return over `period` bars clears the threshold.
                change = rate_of_change(self.ctx.closes(i, p["period"] + 1), p["period"])
                if change is None:
                    return None
                return "enter" if change > float(p["threshold"]) else "exit" if change < 0 else None
            case "macd":
                values = macd(
                    self.ctx.closes(i, (p["slow"] + p["signal_len"]) * 4),
                    p["fast"],
                    p["slow"],
                    p["signal_len"],
                )
                if values is None:
                    return None
                line, signal_line = values
                return "enter" if line > signal_line else "exit"
            case "supertrend":
                window = self.ctx.candles(i, p["period"] * 10)
                result = supertrend(
                    [c.high for c in window],
                    [c.low for c in window],
                    [c.close for c in window],
                    p["period"],
                    float(p["k"]),
                )
                if result is None:
                    return None
                return "enter" if result[0] else "exit"
            case "rsi_trend":
                # Buy a short, sharp dip only while the longer trend is up; sell into the bounce.
                closes = self.ctx.closes(i, max(p["trend"], p["period"] * 5))
                value, trend = rsi(closes[-p["period"] * 5 :], p["period"]), sma(closes, p["trend"])
                if value is None or trend is None:
                    return None
                if value > float(p["upper"]) or close < trend * 0.95:
                    return "exit"
                return "enter" if close > trend and value < float(p["lower"]) else None
            case "volume_breakout":
                prior = self.ctx.candles(i, p["period"] + 1)[:-1]
                channel = donchian([c.high for c in prior], [c.low for c in prior], p["period"])
                volume_z = zscore([c.volume for c in prior] + [candle.volume], p["period"] + 1)
                if channel is None:
                    return None
                exit_low = min(float(c.low) for c in prior[-max(2, p["period"] // 2) :])
                if close < exit_low:
                    return "exit"
                surge = volume_z is not None and volume_z > float(p["k"])
                return "enter" if close > channel[1] and surge else None
            case "orb":
                # Opening-range breakout: the first `period` bars of the session set the range.
                day = candle.open_ts.astimezone(IST).date()
                session = [c for c in self.ctx.candles(i, 200) if c.open_ts.astimezone(IST).date() == day]
                if candle.interval_seconds >= 86400 or len(session) <= p["period"]:
                    return None
                opening = session[: p["period"]]
                high, low = max(float(c.high) for c in opening), min(float(c.low) for c in opening)
                return "enter" if close > high else "exit" if close < low else None
        return None

    def _stopped_out(self, instrument_id: str, price: Decimal) -> bool:
        p = self.ctx.params
        entry = self.ctx.entry_price(instrument_id)
        if entry <= 0:
            return False
        if p["stop_loss"] > 0 and price <= entry * (1 - p["stop_loss"]):
            return True
        peak = max(self._peaks.get(instrument_id, entry), price)
        self._peaks[instrument_id] = peak
        if p["trailing_stop"] > 0 and price <= peak * (1 - p["trailing_stop"]):
            return True
        return p["take_profit"] > 0 and price >= entry * (1 + p["take_profit"])

    def _size(self, instrument_id: str, price: Decimal) -> Decimal:
        instrument = self.ctx.instrument(instrument_id)
        lots = self.ctx.params["capital"] / (price * instrument.contract_multiplier) / instrument.lot_size
        quantity = lots.to_integral_value(rounding=ROUND_FLOOR) * instrument.lot_size
        return quantity if quantity >= instrument.min_quantity else Decimal(0)
