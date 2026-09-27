"""Runs deployed strategies on live bars for paper and live accounts (Chapter 19.6, FR-19014, FR-19021)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from jdquant.marketdata.candles import CandleAggregator
from jdquant.marketdata.records import Candle, Quote, Trade
from jdquant.platform import Platform
from jdquant.strategy.base import CandleHistory
from jdquant.strategy.host import StrategyHost
from jdquant.strategy.templates import TEMPLATES
from jdquant.trading.engine import Deployment, DeploymentState

log = logging.getLogger(__name__)
WARMUP_BARS = 300
ACTIVE = (DeploymentState.RUNNING, DeploymentState.PAUSED)


@dataclass
class _Hosted:
    host: StrategyHost
    history: CandleHistory
    last_open: dict[str, datetime] = field(default_factory=dict)


class DeploymentRunner:
    """Bars come from the venue's candle endpoint when a data source exists, otherwise from quotes."""

    def __init__(self, platform: Platform, data_source: Callable[[str], object | None]):
        self._platform = platform
        self._data_source = data_source
        self.hosted: dict[str, _Hosted] = {}
        self.models = None  # ModelRegistry, attached by the application
        self.entry_gate = None  # e.g. the AI trade monitor, attached by the application
        self._aggregators: dict[tuple[str, int], CandleAggregator] = {}

    def sync(self, deployment: Deployment) -> None:
        hosted = self.hosted.get(deployment.deployment_id)
        if deployment.state in ACTIVE and hosted is None:
            self._start(deployment)
        elif deployment.state not in ACTIVE and hosted is not None:
            hosted.host.stop()
            del self.hosted[deployment.deployment_id]

    def sync_all(self) -> None:
        for deployment in list(self._platform.trading.deployments.values()):
            self.sync(deployment)

    def _start(self, deployment: Deployment) -> None:
        strategy_cls = TEMPLATES.get(deployment.strategy_name)
        if strategy_cls is None:
            self._platform.trading.fail(
                deployment.deployment_id, f"unknown strategy {deployment.strategy_name}"
            )
            return
        history = CandleHistory()
        hosted = _Hosted(
            StrategyHost(
                strategy_cls,
                deployment,
                clock=self._platform.clock,
                bus=self._platform.bus,
                oms=self._platform.oms,
                positions=self._platform.positions,
                trading=self._platform.trading,
                history=history,
                models=self.models,
            ),
            history,
        )
        hosted.host.ctx.entry_gate = self.entry_gate
        for instrument_id in deployment.instruments:  # warm-up without trading (FR-19014 step 6)
            for candle in self._fetch(instrument_id, deployment.bar_interval_seconds, WARMUP_BARS):
                history.append(candle)
                hosted.last_open[instrument_id] = candle.open_ts
        self.hosted[deployment.deployment_id] = hosted

    def _fetch(self, instrument_id: str, interval: int, limit: int) -> list[Candle]:
        source = self._data_source(instrument_id)
        if source is None:
            return []
        try:
            instrument = self._platform.instruments.get(instrument_id)
            return source.fetch_candles(instrument, interval, limit)
        except Exception:
            log.exception("candle fetch failed for %s", instrument_id)
            return []

    def tick(self) -> None:
        """Deliver newly closed venue bars; call outside the platform lock (network I/O)."""
        for hosted in list(self.hosted.values()):
            deployment = hosted.host.deployment
            for instrument_id in deployment.instruments:
                if self._data_source(instrument_id) is None:
                    continue
                for candle in self._fetch(instrument_id, deployment.bar_interval_seconds, 5):
                    self._deliver(hosted, candle)

    def on_quote(self, quote: Quote) -> None:
        """Build bars from quotes for instruments without a candle source (e.g. injected paper quotes)."""
        if self._data_source(quote.instrument_id) is not None:
            return
        for hosted in list(self.hosted.values()):
            deployment = hosted.host.deployment
            if quote.instrument_id not in deployment.instruments:
                continue
            key = (quote.instrument_id, deployment.bar_interval_seconds)
            aggregator = self._aggregators.setdefault(key, CandleAggregator(quote.instrument_id, key[1]))
            trade = Trade(quote.instrument_id, quote.exchange_ts, quote.mid, 0)
            for candle in aggregator.on_trade(trade):
                for h in self.hosted.values():
                    if quote.instrument_id in h.host.deployment.instruments:
                        self._deliver(h, candle)

    def _deliver(self, hosted: _Hosted, candle: Candle) -> None:
        last = hosted.last_open.get(candle.instrument_id)
        if last is not None and candle.open_ts <= last:
            return
        with self._platform.lock:
            hosted.last_open[candle.instrument_id] = candle.open_ts
            hosted.history.append(candle)
            hosted.host.on_bar(candle)
