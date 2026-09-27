"""Hosts one strategy deployment: event delivery and error isolation (FR-24020 – FR-24023)."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from jdquant.core.clock import Clock
from jdquant.core.events import Event, EventBus
from jdquant.marketdata.records import Candle
from jdquant.oms.manager import OrderManager
from jdquant.positions.engine import PositionEngine
from jdquant.strategy.base import CandleHistory, Strategy, StrategyContext, validate_parameters
from jdquant.trading.engine import Deployment, DeploymentState, TradingEngine

log = logging.getLogger(__name__)


class StrategyHost:
    def __init__(
        self,
        strategy_cls: type[Strategy],
        deployment: Deployment,
        *,
        clock: Clock,
        bus: EventBus,
        oms: OrderManager,
        positions: PositionEngine,
        trading: TradingEngine,
        history: CandleHistory,
        max_errors: int = 3,
        error_window: timedelta = timedelta(seconds=60),
    ):
        self.deployment = deployment
        self._clock = clock
        self._trading = trading
        self._max_errors = max_errors
        self._error_window = error_window
        self._errors: deque = deque()
        self.error_count = 0
        params = validate_parameters(strategy_cls.parameters, deployment.parameters)
        self.ctx = StrategyContext(
            deployment_id=deployment.deployment_id,
            account_id=deployment.account_id,
            instruments=deployment.instruments,
            params=params,
            clock=clock,
            oms=oms,
            positions=positions,
            history=history,
        )
        self.strategy = strategy_cls(self.ctx)
        bus.subscribe("order.fill", self._on_fill)
        bus.subscribe("order.state.changed", self._on_order_state)
        self._invoke(self.strategy.on_init)

    @property
    def active(self) -> bool:
        return self.deployment.state in (DeploymentState.RUNNING, DeploymentState.PAUSED)

    def on_bar(self, candle: Candle) -> None:
        if self.active and candle.instrument_id in self.deployment.instruments:
            self._invoke(self.strategy.on_bar, candle)

    def stop(self) -> None:
        self._invoke(self.strategy.on_stop)

    def _on_fill(self, event: Event) -> None:
        fill = event.payload["fill"]
        if fill.deployment_id == self.deployment.deployment_id:
            self._invoke(self.strategy.on_fill, fill)

    def _on_order_state(self, event: Event) -> None:
        order = event.payload["order"]
        if order.deployment_id == self.deployment.deployment_id:
            self._invoke(self.strategy.on_order_update, order)

    def _invoke(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            callback(*args)
        except Exception as exc:
            self.error_count += 1
            log.exception("strategy callback failed in %s", self.deployment.deployment_id)
            now = self._clock.now()
            self._errors.append(now)
            while self._errors and now - self._errors[0] > self._error_window:
                self._errors.popleft()
            if len(self._errors) >= self._max_errors and self.active:
                self._trading.fail(self.deployment.deployment_id, f"strategy error: {exc!r}")
