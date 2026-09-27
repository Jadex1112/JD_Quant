"""The strategies the autopilot deploys.

Research, paper trading and live trading all run these classes with the same parameters, so what was
backtested is exactly what trades.

- `AutopilotStrategy` trades one instrument from a single signal. It is long-only unless the instrument
  can be shorted (futures, forex, spot gold on margin), sizes positions by volatility within its
  allocated capital the way systematic funds size risk, and applies protective stops.
- `RotationStrategy` trades a basket: every few bars it ranks the instruments and holds the best few
  (cross-sectional momentum) or the most oversold quality names (short-term reversal).
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import ROUND_FLOOR, Decimal

from jdquant.core.errors import PlatformError
from jdquant.marketdata.records import Candle
from jdquant.markets.sessions import LONDON, session_for
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.indicators import (
    bollinger,
    donchian,
    ewmac_forecast,
    macd,
    rate_of_change,
    realized_volatility,
    rsi,
    sma,
    supertrend,
    zscore,
)

SIGNALS = (
    "ma_cross", "rsi", "bollinger", "donchian", "ml",
    "momentum", "macd", "supertrend", "rsi_trend", "volume_breakout", "orb", "ewmac", "london_breakout",
)  # fmt: skip
# Signals whose bearish state is a genuine short signal; mean-reversion "exits" are not.
SHORTABLE = (
    "ma_cross", "macd", "supertrend", "momentum", "ewmac", "donchian", "ml", "orb", "london_breakout",
)  # fmt: skip
INTRADAY_ONLY = ("orb", "london_breakout")  # day trades: always flat by the session's intraday cutoff
LONDON_OPEN = time(7, 0)

COMMON = {
    "capital": Param(Decimal, "100000", min=Decimal(0), description="capital allocated, quote currency"),
    "vol_target": Param(
        Decimal, "0", min=Decimal(0), max=Decimal(2),
        description="annualized volatility per position; smaller positions in wilder markets (0 disables)",
    ),
    "bars_per_year": Param(int, 248, min=1, max=1_000_000, description="bars per year for this market"),
    "intraday": Param(bool, False, description="exit before the session's intraday cutoff; no late entries"),
    "trade_after": Param(str, "", description="ISO time; bars closing earlier only warm up"),
}  # fmt: skip


def volatility_scale(closes, vol_target: Decimal, bars_per_year: int) -> Decimal:
    """Fraction of capital to deploy so the position runs at about `vol_target` (at most 1: no leverage)."""
    if vol_target <= 0:
        return Decimal(1)
    vol = realized_volatility(closes, bars_per_year)
    if not vol:
        return Decimal(1)
    return min(Decimal(1), vol_target / Decimal(str(vol)))


class AutopilotStrategy(Strategy):
    name = "autopilot"
    version = "1.3.0"
    description = (
        "AI-selected signal with volatility-based sizing, protective stops and shorting where the "
        "instrument allows it (futures, forex, spot metals)."
    )
    parameters = {
        "signal": Param(str, "ma_cross", description=f"one of {', '.join(SIGNALS)}"),
        "fast": Param(int, 10, min=2, max=500, description="fast average (ma_cross, macd, ewmac base speed)"),
        "slow": Param(int, 30, min=3, max=500, description="slow moving average (ma_cross, macd)"),
        "period": Param(int, 14, min=2, max=500, description="lookback (rsi, bollinger, donchian, ...)"),
        "trend": Param(int, 200, min=2, max=500, description="trend-filter moving average (rsi_trend)"),
        "signal_len": Param(int, 9, min=2, max=100, description="MACD signal-line length"),
        "lower": Param(Decimal, "30", min=Decimal(1), max=Decimal(99), description="RSI entry level"),
        "upper": Param(Decimal, "70", min=Decimal(1), max=Decimal(99), description="RSI exit level"),
        "k": Param(
            Decimal, "2", min=Decimal("0.5"), max=Decimal(5),
            description="Bollinger width, Supertrend ATR multiple, or volume z-score (volume_breakout)",
        ),
        "forecast": Param(Decimal, "5", min=Decimal(0), max=Decimal(20), description="EWMAC entry forecast"),
        "model": Param(str, "", description="registered model name (ml)"),
        "threshold": Param(Decimal, "0.05", min=Decimal(0), max=Decimal("0.49"), description="ml band"),
        "stop_loss": Param(Decimal, "0.08", min=Decimal(0), max=Decimal("0.5"), description="0 disables"),
        "take_profit": Param(Decimal, "0", min=Decimal(0), max=Decimal(5), description="0 disables"),
        "trailing_stop": Param(
            Decimal, "0", min=Decimal(0), max=Decimal("0.5"), description="exit this far from the best price"
        ),
        "allow_short": Param(
            bool, False, description="sell short on bearish signals (futures, forex and metals only)"
        ),
        **COMMON,
    }  # fmt: skip

    def on_init(self) -> None:
        p = self.ctx.params
        if p["signal"] not in SIGNALS:
            raise PlatformError("PARAMETER_INVALID", f"signal must be one of {SIGNALS}")
        self._trade_after = datetime.fromisoformat(p["trade_after"]) if p["trade_after"] else None
        self._best: dict[str, Decimal] = {}  # best price since entry, for the trailing stop

    def on_bar(self, candle: Candle) -> None:
        if self._trade_after is not None and candle.close_ts < self._trade_after:
            return
        i = candle.instrument_id
        if self.ctx.open_orders(i):
            return
        p = self.ctx.params
        position = self.ctx.position(i)
        day_trade = p["intraday"] or p["signal"] in INTRADAY_ONLY
        late = day_trade and session_for(self.ctx.instrument(i)).past_intraday_cutoff(candle.close_ts)
        view = self.decide(candle)
        if position > 0:
            if late or self._stopped_out(i, candle.close, 1) or view == "exit":
                self.ctx.sell(i, position)
            return
        if position < 0:
            if late or self._stopped_out(i, candle.close, -1) or view == "enter":
                self.ctx.buy(i, -position)
            return
        self._best.pop(i, None)
        if late:
            return
        if view == "enter":
            quantity = self._size(i, candle.close)
            if quantity > 0:
                self.ctx.buy(i, quantity)
        elif view == "exit" and p["allow_short"] and p["signal"] in SHORTABLE:
            quantity = self._size(i, candle.close)
            if quantity > 0:
                self.ctx.sell(i, quantity)

    def decide(self, candle: Candle) -> str | None:
        """'enter' (bullish), 'exit' (bearish) or None (no view) from the configured signal."""
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
                closes = self.ctx.closes(i, (p["slow"] + p["signal_len"]) * 4)
                values = macd(closes, p["fast"], p["slow"], p["signal_len"])
                if values is None:
                    return None
                line, signal_line = values
                return "enter" if line > signal_line else "exit"
            case "supertrend":
                window = self.ctx.candles(i, p["period"] * 10)
                result = supertrend(
                    [c.high for c in window], [c.low for c in window], [c.close for c in window],
                    p["period"], float(p["k"]),
                )  # fmt: skip
                if result is None:
                    return None
                return "enter" if result[0] else "exit"
            case "ewmac":
                forecast = ewmac_forecast(self.ctx.closes(i, p["fast"] * 16 + 60), p["fast"])
                if forecast is None:
                    return None
                threshold = float(p["forecast"])
                return "enter" if forecast > threshold else "exit" if forecast < -threshold else None
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
                tz = session_for(self.ctx.instrument(i)).tz
                day = candle.open_ts.astimezone(tz).date()
                session = [c for c in self.ctx.candles(i, 200) if c.open_ts.astimezone(tz).date() == day]
                if candle.interval_seconds >= 86400 or len(session) <= p["period"]:
                    return None
                opening = session[: p["period"]]
                high, low = max(float(c.high) for c in opening), min(float(c.low) for c in opening)
                return "enter" if close > high else "exit" if close < low else None
            case "london_breakout":
                return self._london_breakout(candle)
        return None

    def _london_breakout(self, candle: Candle) -> str | None:
        """Trade the break of the Asian-session range (00:00-07:00 London) during the London morning.

        `period` is how many hours after 07:00 London a breakout may still be entered.
        """
        i, p = candle.instrument_id, self.ctx.params
        if candle.interval_seconds > 3600:
            return None
        now = candle.close_ts.astimezone(LONDON)
        hours_open = (now.hour * 60 + now.minute - LONDON_OPEN.hour * 60) / 60
        if not 0 <= hours_open <= p["period"]:
            return None
        per_hour = max(1, 3600 // candle.interval_seconds)
        asian = [
            c
            for c in self.ctx.candles(i, per_hour * (7 + p["period"]) + 2)
            if c.open_ts.astimezone(LONDON).date() == now.date()
            and c.open_ts.astimezone(LONDON).time() < LONDON_OPEN
        ]
        if len(asian) < per_hour * 7 * 0.8:  # the range needs most of the night's bars
            return None
        high, low = max(float(c.high) for c in asian), min(float(c.low) for c in asian)
        close = float(candle.close)
        return "enter" if close > high else "exit" if close < low else None

    def _stopped_out(self, instrument_id: str, price: Decimal, direction: int) -> bool:
        """Fixed stop from entry, trailing stop from the best price since entry, and take-profit."""
        p = self.ctx.params
        entry = self.ctx.entry_price(instrument_id)
        if entry <= 0:
            return False
        move = (price - entry) / entry * direction  # positive when the position is winning
        if p["stop_loss"] > 0 and move <= -p["stop_loss"]:
            return True
        best = self._best.get(instrument_id, entry)
        best = max(best, price) if direction > 0 else min(best, price)
        self._best[instrument_id] = best
        if p["trailing_stop"] > 0 and (best - price) / best * direction >= p["trailing_stop"]:
            return True
        return p["take_profit"] > 0 and move >= p["take_profit"]

    def _size(self, instrument_id: str, price: Decimal) -> Decimal:
        p = self.ctx.params
        scale = volatility_scale(self.ctx.closes(instrument_id, 61), p["vol_target"], p["bars_per_year"])
        return whole_lots(self.ctx.instrument(instrument_id), p["capital"] * scale, price)


def whole_lots(instrument, capital: Decimal, price: Decimal) -> Decimal:
    lots = capital / (price * instrument.contract_multiplier) / instrument.lot_size
    quantity = lots.to_integral_value(rounding=ROUND_FLOOR) * instrument.lot_size
    return quantity if quantity >= instrument.min_quantity else Decimal(0)


class RotationStrategy(Strategy):
    """Cross-sectional selection across a basket, rebalanced on a fixed schedule (long-only).

    momentum: hold the `hold` instruments with the best return over `lookback` bars, skipping the most
      recent `skip` bars (short-term noise), and only those whose return is positive (absolute momentum).
    reversal: hold the `hold` instruments that fell most over `lookback` bars while still above their
      `trend`-bar average (oversold quality names), for one rebalance period.
    """

    name = "rotation"
    version = "1.0.0"
    description = "Cross-sectional momentum or short-term reversal across a basket, volatility-weighted."
    parameters = {
        "mode": Param(str, "momentum", description="momentum or reversal"),
        "lookback": Param(int, 126, min=2, max=500, description="ranking window in bars"),
        "skip": Param(int, 21, min=0, max=100, description="most recent bars ignored by momentum"),
        "hold": Param(int, 3, min=1, max=50, description="instruments held at once"),
        "rebalance": Param(int, 21, min=1, max=500, description="bars between rebalances"),
        "trend": Param(int, 200, min=2, max=500, description="quality filter average (reversal)"),
        **COMMON,
    }

    def on_init(self) -> None:
        p = self.ctx.params
        if p["mode"] not in ("momentum", "reversal"):
            raise PlatformError("PARAMETER_INVALID", "mode must be momentum or reversal")
        self._trade_after = datetime.fromisoformat(p["trade_after"]) if p["trade_after"] else None
        self._trigger = sorted(self.ctx.instruments)[-1]  # rebalance once all of the bar's prices are in
        self._bars = 0

    def on_bar(self, candle: Candle) -> None:
        if candle.instrument_id != self._trigger:
            return
        if self._trade_after is not None and candle.close_ts < self._trade_after:
            return
        self._bars += 1
        if (self._bars - 1) % self.ctx.params["rebalance"] or self.ctx.open_orders():
            return
        self.rebalance()

    def rank(self) -> list[str]:
        p = self.ctx.params
        scores: dict[str, float] = {}
        for i in self.ctx.instruments:
            closes = self.ctx.closes(i, max(p["lookback"] + p["skip"] + 1, p["trend"]))
            if p["mode"] == "momentum":
                if len(closes) < p["lookback"] + p["skip"] + 1:
                    continue
                window = closes[: len(closes) - p["skip"]] if p["skip"] else closes
                change = rate_of_change(window, p["lookback"])
                if change is not None and change > 0:
                    scores[i] = change
            else:
                change = rate_of_change(closes, p["lookback"])
                trend = sma(closes, p["trend"])
                if change is not None and trend is not None and change < 0 and float(closes[-1]) > trend:
                    scores[i] = -change  # the bigger the drop, the higher the rank
        return sorted(scores, key=scores.get, reverse=True)[: p["hold"]]

    def rebalance(self) -> None:
        p = self.ctx.params
        chosen = set(self.rank())
        slot = p["capital"] / p["hold"]
        for i in sorted(self.ctx.instruments):
            position = self.ctx.position(i)
            if i not in chosen:
                if position > 0:
                    self.ctx.sell(i, position)
                continue
            closes = self.ctx.closes(i, 61)
            if not closes:
                continue
            scale = volatility_scale(closes, p["vol_target"], p["bars_per_year"])
            target = whole_lots(self.ctx.instrument(i), slot * scale, closes[-1])
            delta = target - position
            if target and abs(delta) < target * Decimal("0.2"):
                continue  # close enough: avoid paying costs to trim small differences
            if delta > 0:
                self.ctx.buy(i, delta)
            elif delta < 0:
                self.ctx.sell(i, -delta)


class AiTraderStrategy(Strategy):
    """The AI decides the trades, choosing among the signals of strategies that passed walk-forward tests.

    This class never opens a position. On every bar it works out what each tested strategy on its panel
    would hold now (long, short or flat) and keeps the AI's positions inside their stops and session
    cutoffs. The AI trade monitor reads those views each minute, decides, and sends the orders; it may only
    take a direction that at least one panel strategy currently holds.
    """

    name = "ai_trader"
    version = "1.0.0"
    description = "Trades chosen by the AI among the live signals of walk-forward-tested strategies."
    parameters = {
        "panel": Param(str, "[]", description="JSON list of tested strategies: key, label, params, stats"),
        "stop_loss": Param(Decimal, "0.08", min=Decimal(0), max=Decimal("0.5"), description="0 disables"),
        "take_profit": Param(Decimal, "0", min=Decimal(0), max=Decimal(5), description="0 disables"),
        "trailing_stop": Param(
            Decimal, "0", min=Decimal(0), max=Decimal("0.5"), description="exit this far from the best price"
        ),
        "allow_short": Param(bool, False, description="the instrument can be sold short"),
        **COMMON,
    }

    def on_init(self) -> None:
        import json

        from jdquant.strategy.base import StrategyContext, validate_parameters

        self.panel = json.loads(self.ctx.params["panel"])
        self._members: list[tuple[dict, AutopilotStrategy]] = []
        for spec in self.panel:
            params = validate_parameters(AutopilotStrategy.parameters, spec["params"])
            ctx = StrategyContext(
                deployment_id=self.ctx.deployment_id,
                account_id=self.ctx.account_id,
                instruments=self.ctx.instruments,
                params=params,
                clock=self.ctx._clock,
                oms=self.ctx._oms,
                positions=self.ctx._positions,
                history=self.ctx._history,
                models=None,
            )
            member = AutopilotStrategy(ctx)
            member.on_init()
            self._members.append((spec, member))
        self._held: dict[str, dict[str, str]] = {}  # instrument -> member key -> LONG/SHORT/FLAT
        self._ready: set[str] = set()
        self._best: dict[str, Decimal] = {}
        self.stopped_at: dict[str, datetime] = {}

    # ---- the panel's views ---------------------------------------------------------------------

    def _advance(self, candle: Candle) -> None:
        held = self._held.setdefault(candle.instrument_id, {})
        for spec, member in self._members:
            view = member.decide(candle)
            if view == "enter":
                held[spec["key"]] = "LONG"
            elif view == "exit":
                short = member.ctx.params["allow_short"] and member.ctx.params["signal"] in SHORTABLE
                held[spec["key"]] = "SHORT" if short else "FLAT"
            else:
                held.setdefault(spec["key"], "FLAT")

    def views(self, instrument_id: str) -> list[dict]:
        """What each panel strategy would hold now, replaying history the first time it is asked."""
        if instrument_id not in self._ready:
            self._replay(instrument_id)
        held = self._held.get(instrument_id, {})
        return [{**spec, "view": held.get(spec["key"], "FLAT")} for spec, _ in self._members]

    def _replay(self, instrument_id: str) -> None:
        from jdquant.strategy.base import CandleHistory

        candles = self.ctx.candles(instrument_id)
        scratch = CandleHistory()
        for _, member in self._members:
            member.ctx._history = scratch
        try:
            for candle in candles:
                scratch.append(candle)
                self._advance(candle)
        finally:
            for _, member in self._members:
                member.ctx._history = self.ctx._history
        self._ready.add(instrument_id)

    # ---- bars: update views, keep positions inside their limits ----------------------------------

    def on_bar(self, candle: Candle) -> None:
        i = candle.instrument_id
        if i in self._ready:
            self._advance(candle)
        else:
            self._replay(i)
        position = self.ctx.position(i)
        if position == 0:
            self._best.pop(i, None)
            return
        if self.ctx.open_orders(i):
            return
        p = self.ctx.params
        direction = 1 if position > 0 else -1
        late = p["intraday"] and session_for(self.ctx.instrument(i)).past_intraday_cutoff(candle.close_ts)
        if late or self._stopped_out(i, candle.close, direction):
            self.stopped_at[i] = candle.close_ts
            if position > 0:
                self.ctx.sell(i, position)
            else:
                self.ctx.buy(i, -position)

    def _stopped_out(self, instrument_id: str, price: Decimal, direction: int) -> bool:
        p = self.ctx.params
        entry = self.ctx.entry_price(instrument_id)
        if entry <= 0:
            return False
        move = (price - entry) / entry * direction
        if p["stop_loss"] > 0 and move <= -p["stop_loss"]:
            return True
        best = self._best.get(instrument_id, entry)
        best = max(best, price) if direction > 0 else min(best, price)
        self._best[instrument_id] = best
        if p["trailing_stop"] > 0 and (best - price) / best * direction >= p["trailing_stop"]:
            return True
        return p["take_profit"] > 0 and move >= p["take_profit"]

    def max_quantity(self, instrument_id: str, price: Decimal) -> Decimal:
        """The largest position the AI may take: volatility-targeted, never leveraged."""
        p = self.ctx.params
        scale = volatility_scale(self.ctx.closes(instrument_id, 61), p["vol_target"], p["bars_per_year"])
        return whole_lots(self.ctx.instrument(instrument_id), p["capital"] * scale, price)
