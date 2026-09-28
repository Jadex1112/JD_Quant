"""Strategy template library (FR-24002)."""

from __future__ import annotations

from decimal import Decimal

from jdquant.core.errors import PlatformError
from jdquant.marketdata.records import Candle
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.indicators import bollinger, donchian, rsi, sma


class MovingAverageCrossover(Strategy):
    name = "ma_crossover"
    description = "Long (optionally short) when the fast SMA is above (below) the slow SMA."
    parameters = {
        "fast": Param(int, 10, min=2, max=500, description="Fast SMA period"),
        "slow": Param(int, 30, min=3, max=2000, description="Slow SMA period"),
        "quantity": Param(Decimal, "1", min=Decimal("0"), description="Position size"),
        "allow_short": Param(bool, False, description="Take short positions on bearish crossovers"),
    }

    def on_bar(self, candle: Candle) -> None:
        p = self.ctx.params
        closes = self.ctx.closes(candle.instrument_id, p["slow"])
        fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
        if fast is None or slow is None or self.ctx.open_orders(candle.instrument_id):
            return
        if fast > slow:
            target = p["quantity"]
        else:
            target = -p["quantity"] if p["allow_short"] else Decimal(0)
        self.ctx.order_target(candle.instrument_id, target)


class RsiMeanReversion(Strategy):
    name = "rsi_mean_reversion"
    description = "Buy when RSI falls below the lower band, exit when it rises above the upper band."
    parameters = {
        "period": Param(int, 14, min=2, max=200),
        "lower": Param(Decimal, "30", min=Decimal(1), max=Decimal(99)),
        "upper": Param(Decimal, "70", min=Decimal(1), max=Decimal(99)),
        "quantity": Param(Decimal, "1", min=Decimal("0")),
    }

    def on_bar(self, candle: Candle) -> None:
        p = self.ctx.params
        value = rsi(self.ctx.closes(candle.instrument_id, p["period"] * 5), p["period"])
        if value is None or self.ctx.open_orders(candle.instrument_id):
            return
        position = self.ctx.position(candle.instrument_id)
        if position == 0 and value < float(p["lower"]):
            self.ctx.buy(candle.instrument_id, p["quantity"])
        elif position > 0 and value > float(p["upper"]):
            self.ctx.sell(candle.instrument_id, position)


class BollingerReversion(Strategy):
    name = "bollinger_reversion"
    description = "Buy below the lower Bollinger band, exit at the middle band."
    parameters = {
        "period": Param(int, 20, min=2, max=500),
        "k": Param(Decimal, "2", min=Decimal("0.5"), max=Decimal(5)),
        "quantity": Param(Decimal, "1", min=Decimal("0")),
    }

    def on_bar(self, candle: Candle) -> None:
        p = self.ctx.params
        bands = bollinger(self.ctx.closes(candle.instrument_id, p["period"]), p["period"], float(p["k"]))
        if bands is None or self.ctx.open_orders(candle.instrument_id):
            return
        lower, middle, _ = bands
        position = self.ctx.position(candle.instrument_id)
        close = float(candle.close)
        if position == 0 and close < lower:
            self.ctx.buy(candle.instrument_id, p["quantity"])
        elif position > 0 and close >= middle:
            self.ctx.sell(candle.instrument_id, position)


class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    description = "Enter long on a breakout above the prior N-bar high, exit below the prior N-bar low."
    parameters = {
        "period": Param(int, 20, min=2, max=500),
        "quantity": Param(Decimal, "1", min=Decimal("0")),
    }

    def on_bar(self, candle: Candle) -> None:
        p = self.ctx.params
        prior = self.ctx.candles(candle.instrument_id, p["period"] + 1)[:-1]
        channel = donchian([c.high for c in prior], [c.low for c in prior], p["period"])
        if channel is None or self.ctx.open_orders(candle.instrument_id):
            return
        low, high = channel
        position = self.ctx.position(candle.instrument_id)
        if position == 0 and float(candle.close) > high:
            self.ctx.buy(candle.instrument_id, p["quantity"])
        elif position > 0 and float(candle.close) < low:
            self.ctx.sell(candle.instrument_id, position)


class MlSignal(Strategy):
    name = "ml_signal"
    description = (
        "Long when a production model's up-probability clears 0.5 + threshold, flat below 0.5 - threshold."
    )
    parameters = {
        "model": Param(str, "", description="Registered model name (PRODUCTION version is used)"),
        "threshold": Param(Decimal, "0.05", min=Decimal(0), max=Decimal("0.49")),
        "quantity": Param(Decimal, "1", min=Decimal("0")),
    }

    def on_bar(self, candle: Candle) -> None:
        p = self.ctx.params
        if self.ctx.open_orders(candle.instrument_id):
            return
        try:
            probability = self.ctx.predict(p["model"], candle.instrument_id)
        except PlatformError as exc:
            if exc.code == "INPUT_INVALID":
                return  # still warming up
            raise
        if probability > 0.5 + float(p["threshold"]):
            self.ctx.order_target(candle.instrument_id, p["quantity"])
        elif probability < 0.5 - float(p["threshold"]):
            self.ctx.order_target(candle.instrument_id, Decimal(0))


def _autopilot() -> tuple[type[Strategy], ...]:
    from jdquant.autopilot.strategy import AiTraderStrategy, AutopilotStrategy, RotationStrategy
    from jdquant.strategy.rules import RuleStrategy

    return AutopilotStrategy, RotationStrategy, AiTraderStrategy, RuleStrategy


def _flow() -> tuple[type[Strategy], ...]:
    from jdquant.strategy.flow import KronosForecast, LiquidityWallBounce, OrderFlowMomentum, VwapReclaim

    return VwapReclaim, OrderFlowMomentum, LiquidityWallBounce, KronosForecast


TEMPLATES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (
        MovingAverageCrossover,
        RsiMeanReversion,
        BollingerReversion,
        DonchianBreakout,
        MlSignal,
        *_autopilot(),
        *_flow(),
    )
}
