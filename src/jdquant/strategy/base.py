"""Strategy programming contract (Chapter 24.4)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from jdquant.core.clock import Clock
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.core.types import Side
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Candle
from jdquant.oms.manager import OrderManager
from jdquant.oms.orders import Fill, Order, OrderRequest, OrderSource, OrderType
from jdquant.positions.engine import PositionEngine


@dataclass(frozen=True)
class Param:
    type: type
    default: Any
    min: Any = None
    max: Any = None
    choices: tuple[Any, ...] | None = None
    description: str = ""


def validate_parameters(schema: dict[str, Param], values: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults and report every violation (FR-19011)."""
    violations: list[dict[str, str]] = []
    result: dict[str, Any] = {}
    for name in values.keys() - schema.keys():
        violations.append({"field": name, "message": "unknown parameter"})
    for name, param in schema.items():
        raw = values.get(name, param.default)
        try:
            value = _coerce(raw, param.type)
        except (TypeError, ValueError, InvalidOperation):
            violations.append({"field": name, "message": f"expected {param.type.__name__}"})
            continue
        if param.min is not None and value < param.min:
            violations.append({"field": name, "message": f"must be >= {param.min}"})
        if param.max is not None and value > param.max:
            violations.append({"field": name, "message": f"must be <= {param.max}"})
        if param.choices is not None and value not in param.choices:
            violations.append({"field": name, "message": f"must be one of {list(param.choices)}"})
        result[name] = value
    if violations:
        raise ValidationError("PARAMETER_INVALID", violations)
    return result


def _coerce(raw: Any, target: type) -> Any:
    if target is Decimal:
        if isinstance(raw, float):
            raise TypeError("float not allowed for Decimal parameters")
        return Decimal(str(raw))
    if target is bool:
        if isinstance(raw, bool):
            return raw
        raise TypeError("expected bool")
    if target is int and (isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer())):
        raise TypeError("expected int")
    return target(raw)


class CandleHistory:
    """Closed candles only, appended after each bar closes: no look-ahead by construction (FR-25003)."""

    def __init__(self, max_length: int = 10_000):
        self._max = max_length
        self._candles: dict[str, list[Candle]] = defaultdict(list)

    def append(self, candle: Candle) -> None:
        series = self._candles[candle.instrument_id]
        series.append(candle)
        if len(series) > self._max:
            del series[: len(series) - self._max]

    def window(self, instrument_id: str, length: int | None = None) -> list[Candle]:
        series = self._candles[instrument_id]
        return list(series if length is None else series[-length:])


class StrategyContext:
    def __init__(
        self,
        *,
        deployment_id: str,
        account_id: str,
        instruments: list[str],
        params: dict[str, Any],
        clock: Clock,
        oms: OrderManager,
        positions: PositionEngine,
        history: CandleHistory,
        models: Any = None,
    ):
        self.deployment_id = deployment_id
        self.account_id = account_id
        self.instruments = list(instruments)
        self.params = params
        self._clock = clock
        self._oms = oms
        self._positions = positions
        self._history = history
        self._models = models
        self.state: dict[str, Any] = {}
        self.logger = logging.getLogger(f"jdquant.strategy.{deployment_id}")

    def predict(self, model: str, instrument_id: str) -> float:
        """Score the latest closed bar with the model's PRODUCTION version (Chapter 59)."""
        if self._models is None:
            raise PlatformError("MODELS_UNAVAILABLE", "no model registry is attached to this deployment")
        window = self.candles(instrument_id)
        return self._models.predict(
            model, window, instrument_id=instrument_id, record=self._record_inference
        ).value

    _record_inference = True

    def now(self) -> datetime:
        return self._clock.now()

    def candles(self, instrument_id: str, length: int | None = None) -> list[Candle]:
        return self._history.window(instrument_id, length)

    def closes(self, instrument_id: str, length: int | None = None) -> list[Decimal]:
        return [c.close for c in self.candles(instrument_id, length)]

    def position(self, instrument_id: str) -> Decimal:
        pos = self._positions.get_or_create(self.account_id, instrument_id, self.deployment_id)
        return pos.quantity

    def instrument(self, instrument_id: str) -> Instrument:
        return self._oms.instruments.get(instrument_id)

    def entry_price(self, instrument_id: str) -> Decimal:
        """Average entry price of this deployment's position (zero when flat)."""
        position = self._positions.get_or_create(self.account_id, instrument_id, self.deployment_id)
        return position.average_entry_price

    def open_orders(self, instrument_id: str | None = None) -> list[Order]:
        return [
            o
            for o in self._oms.list_orders(deployment_id=self.deployment_id, working_only=True)
            if instrument_id is None or o.instrument_id == instrument_id
        ]

    def buy(self, instrument_id: str, quantity: Decimal, limit_price: Decimal | None = None, **kw) -> Order:
        return self._submit(instrument_id, Side.BUY, quantity, limit_price, **kw)

    def sell(self, instrument_id: str, quantity: Decimal, limit_price: Decimal | None = None, **kw) -> Order:
        return self._submit(instrument_id, Side.SELL, quantity, limit_price, **kw)

    def order_target(self, instrument_id: str, target: Decimal) -> Order | None:
        delta = target - self.position(instrument_id)
        if delta == 0:
            return None
        return self.buy(instrument_id, delta) if delta > 0 else self.sell(instrument_id, -delta)

    def cancel_all(self) -> None:
        self._oms.cancel_all(deployment_id=self.deployment_id)

    def log(self, message: str, *args: Any) -> None:
        self.logger.info(message, *args)

    def _submit(
        self, instrument_id: str, side: Side, quantity: Decimal, limit_price: Decimal | None, **kw: Any
    ) -> Order:
        return self._oms.submit(
            OrderRequest(
                account_id=self.account_id,
                instrument_id=instrument_id,
                side=side,
                order_type=OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
                quantity=quantity,
                limit_price=limit_price,
                deployment_id=self.deployment_id,
                source=OrderSource.STRATEGY,
                submitter=f"deployment:{self.deployment_id}",
                **kw,
            )
        )


class Strategy:
    """Base class; subclasses override any subset of the callbacks."""

    name: ClassVar[str] = "strategy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Param]] = {}

    def __init__(self, ctx: StrategyContext):
        self.ctx = ctx

    def on_init(self) -> None: ...
    def on_bar(self, candle: Candle) -> None: ...
    def on_fill(self, fill: Fill) -> None: ...
    def on_order_update(self, order: Order) -> None: ...
    def on_stop(self) -> None: ...
