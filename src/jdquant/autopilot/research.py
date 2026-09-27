"""Automated strategy research: generate candidates, walk-forward test them, reject luck, allocate capital.

Protocol, per instrument:
1. The first `train_fraction` of history is never traded; it seeds indicators and trains ML models.
2. The rest is cut into `folds` consecutive out-of-sample windows. Every candidate is backtested on each
   window starting flat, with ML models retrained only on data before the window (walk-forward).
3. The first folds-1 windows are the validation record used for selection; the last window is a holdout
   that the selection never looks at except as a final pass/fail check.
4. Because many candidates are tried, a validation Sharpe ratio is deflated for the number of trials
   (Bailey & López de Prado, 2014): it must beat what the best of that many skill-less strategies would
   reach by chance before it counts as evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from statistics import NormalDist
from typing import Any

import numpy as np

from jdquant.ai.features import DEFAULT_FEATURES, build_feature_set
from jdquant.ai.optimizer import optimize
from jdquant.ai.training import LinearModel, TrainingConfig, train
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import PlatformError
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Candle
from jdquant.markets.india import IndiaEquityFees, Product

EULER_GAMMA = 0.5772156649
NSE_TRADING_DAYS = 248
NSE_SESSION_SECONDS = 22_500  # 09:15-15:30


@dataclass(frozen=True)
class Candidate:
    signal: str
    params: tuple[tuple[str, Any], ...] = ()
    horizon: int = 1  # ML label horizon in bars

    @property
    def key(self) -> str:
        args = ",".join(f"{k}={v}" for k, v in self.params)
        return f"{self.signal}({args})" + (f"[h={self.horizon}]" if self.signal == "ml" else "")

    @property
    def label(self) -> str:
        p = dict(self.params)
        match self.signal:
            case "ma_cross":
                return f"Trend: {p['fast']}/{p['slow']}-bar moving-average crossover"
            case "rsi":
                return f"Mean reversion: RSI({p['period']}) buy < {p['lower']}, sell > {p['upper']}"
            case "bollinger":
                return f"Mean reversion: Bollinger({p['period']}, {p['k']}σ)"
            case "donchian":
                return f"Breakout: {p['period']}-bar channel"
            case "ml":
                return f"ML: logistic model, {self.horizon}-bar horizon, ±{p['threshold']} band"
        return self.key


def candidate_grid() -> list[Candidate]:
    grid = [
        Candidate("ma_cross", (("fast", f), ("slow", s))) for f, s in ((5, 20), (10, 30), (10, 50), (20, 60))
    ]
    grid += [
        Candidate("rsi", (("period", p), ("lower", Decimal(lo)), ("upper", Decimal(up))))
        for p, lo, up in ((14, 30, 60), (14, 30, 70), (7, 25, 55), (21, 35, 65))
    ]
    grid += [
        Candidate("bollinger", (("period", p), ("k", Decimal(k))))
        for p, k in ((20, "2"), (20, "1.5"), (10, "2"))
    ]
    grid += [Candidate("donchian", (("period", p),)) for p in (10, 20, 40)]
    grid += [
        Candidate("ml", (("threshold", Decimal(t)),), horizon=h) for h in (1, 5) for t in ("0.02", "0.05")
    ]
    return grid


@dataclass
class ResearchConfig:
    interval_seconds: int = 86400
    capital: Decimal = Decimal(100_000)  # total budget to allocate across selections
    stop_loss: Decimal = Decimal("0.08")
    take_profit: Decimal = Decimal(0)
    intraday: bool = False
    product: Product = Product.CNC
    slippage_bps: Decimal = Decimal(5)
    folds: int = 4
    train_fraction: float = 0.4
    warmup: int = 100
    min_trades: int = 5
    min_sharpe: float = 0.5
    max_drawdown: float = 0.25
    min_dsr: float = 0.75
    min_positive_folds: int = 2
    require_positive_holdout: bool = True
    max_positions: int = 5
    max_weight: float = 0.4

    @property
    def periods_per_year(self) -> float:
        if self.interval_seconds >= 86400:
            return NSE_TRADING_DAYS * 86400 / self.interval_seconds
        return NSE_TRADING_DAYS * max(1, NSE_SESSION_SECONDS // self.interval_seconds)


@dataclass
class Evaluation:
    instrument_id: str
    candidate: Candidate
    validation: dict[str, Any]
    holdout: dict[str, Any]
    fold_returns: list[float]
    dsr: float | None = None
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    equity: list[tuple[datetime, float]] = field(default_factory=list)  # out-of-sample, starts at 1.0
    returns: list[float] = field(default_factory=list)  # validation per-bar returns

    @property
    def score(self) -> float:
        return (self.dsr or 0.0) * 10 + (self.validation.get("sharpe") or 0.0)

    def summary(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "candidate": self.candidate.key,
            "label": self.candidate.label,
            "signal": self.candidate.signal,
            "validation": self.validation,
            "holdout": self.holdout,
            "fold_returns": self.fold_returns,
            "dsr": self.dsr,
            "passed": self.passed,
            "reasons": self.reasons,
        }


@dataclass
class Selection:
    evaluation: Evaluation
    weight: float
    capital: Decimal


@dataclass
class ResearchResult:
    evaluations: list[Evaluation]
    selections: list[Selection]
    trials: int
    skipped: dict[str, str]


class StaticModels:
    """Serves in-memory models to the ML signal during backtests (no registry, nothing recorded)."""

    def __init__(self, model: LinearModel, feature_set):
        self._model, self._features = model, feature_set

    def predict(self, name: str, window, *, instrument_id: str | None = None, record: bool = True):
        vector = self._features.vector(window)
        if vector is None:
            raise PlatformError("INPUT_INVALID", "not enough history for the features")
        return _Score(self._model.score(vector))


@dataclass
class _Score:
    value: float


Progress = Callable[[int, int, str], None]


def research(
    instruments: list[Instrument],
    candles: dict[str, list[Candle]],
    config: ResearchConfig,
    *,
    candidates: list[Candidate] | None = None,
    progress: Progress | None = None,
) -> ResearchResult:
    grid = candidates or candidate_grid()
    usable, skipped = [], {}
    minimum = config.warmup + config.folds * 30
    for instrument in instruments:
        n = len(candles.get(instrument.instrument_id, []))
        if n < minimum:
            skipped[instrument.instrument_id] = f"only {n} bars of history; at least {minimum} are needed"
        else:
            usable.append(instrument)
    total, done = len(usable) * len(grid), 0
    evaluations: list[Evaluation] = []
    for instrument in usable:
        series = candles[instrument.instrument_id]
        for candidate in grid:
            if progress:
                progress(done, total, f"{instrument.instrument_id}: {candidate.label}")
            try:
                evaluations.append(walk_forward(instrument, series, candidate, config))
            except PlatformError as exc:
                skipped.setdefault(f"{instrument.instrument_id} {candidate.key}", exc.message)
            done += 1
    trials = len(evaluations)
    _deflate(evaluations, trials)
    for evaluation in evaluations:
        _apply_gates(evaluation, config)
    selections = _select(evaluations, config)
    if progress:
        progress(total, total, "done")
    return ResearchResult(evaluations, selections, trials, skipped)


# ---- walk-forward -------------------------------------------------------------------------------


def fold_bounds(n: int, config: ResearchConfig) -> list[tuple[int, int]]:
    start = max(int(n * config.train_fraction), config.warmup)
    size = (n - start) // config.folds
    bounds = [(start + k * size, start + (k + 1) * size) for k in range(config.folds)]
    bounds[-1] = (bounds[-1][0], n)
    return bounds


def walk_forward(
    instrument: Instrument, series: list[Candle], candidate: Candidate, config: ResearchConfig
) -> Evaluation:
    fold_curves: list[list[float]] = []
    fold_times: list[list[datetime]] = []
    entries, wins, closed = [], 0, 0
    for fold_start, fold_end in fold_bounds(len(series), config):
        params = strategy_parameters(candidate, config.capital, config)
        params["trade_after"] = series[fold_start - 1].close_ts.isoformat()
        models = None
        if candidate.signal == "ml":
            model, feature_set = train_signal_model(series[:fold_start], candidate.horizon)
            models = StaticModels(model, feature_set)
            params["model"] = "walk-forward"
        window = series[fold_start - config.warmup : fold_end]
        result = run_backtest(
            BacktestConfig(
                strategy="autopilot",
                instruments=[instrument],
                candles={instrument.instrument_id: window},
                parameters=params,
                initial_capital=config.capital,
                base_currency=instrument.quote_asset,
                fees=fees_for(instrument, config.product),
                slippage_bps=config.slippage_bps,
                periods_per_year=config.periods_per_year,
                models=models,
            )
        )
        curve = result.equity_curve[config.warmup - 1 :]
        fold_curves.append([float(v) for _, v in curve])
        fold_times.append([t for t, _ in curve])
        entries.append(sum(1 for f in result.fills if f.side.sign > 0))
        closed += len(result.trades)
        wins += sum(1 for t in result.trades if t.net_pnl > 0)

    fold_returns = [curve[-1] / curve[0] - 1 for curve in fold_curves]
    per_bar = [np.diff(c) / np.array(c[:-1]) for c in fold_curves]
    validation_returns = np.concatenate(per_bar[:-1]) if len(per_bar) > 1 else np.array([])
    holdout_returns = per_bar[-1]
    validation = _stats(validation_returns, config.periods_per_year)
    validation["trades"] = sum(entries[:-1])
    validation["positive_folds"] = sum(1 for r in fold_returns[:-1] if r > 0)
    validation["win_rate"] = wins / closed if closed else None
    holdout = _stats(holdout_returns, config.periods_per_year)
    holdout["trades"] = entries[-1]

    equity, level = [], 1.0
    for times, curve in zip(fold_times, fold_curves, strict=True):
        base = curve[0]
        for t, v in zip(times, curve, strict=True):
            equity.append((t, level * v / base))
        level = equity[-1][1]
    return Evaluation(
        instrument.instrument_id,
        candidate,
        validation,
        holdout,
        [float(r) for r in fold_returns],
        equity=equity,
        returns=[float(r) for r in validation_returns],
    )


def train_signal_model(history: list[Candle], horizon: int):
    feature_set = build_feature_set("autopilot-features", list(DEFAULT_FEATURES))
    model, _report = train(feature_set, history, TrainingConfig(horizon=horizon, test_fraction=0.2))
    return model, feature_set


def strategy_parameters(candidate: Candidate, capital: Decimal, config: ResearchConfig) -> dict[str, Any]:
    params: dict[str, Any] = {"signal": candidate.signal, **dict(candidate.params)}
    params.update(
        capital=str(capital),
        stop_loss=str(config.stop_loss),
        take_profit=str(config.take_profit),
        intraday=config.intraday,
    )
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in params.items()}


def fees_for(instrument: Instrument, product: Product) -> FeeSchedule:
    return IndiaEquityFees(product=product) if instrument.venue == "NSE" else FeeSchedule()


# ---- statistics ---------------------------------------------------------------------------------


def _stats(returns: np.ndarray, periods_per_year: float) -> dict[str, Any]:
    if len(returns) == 0:
        return {"return": None, "sharpe": None, "max_drawdown": None, "bars": 0}
    equity = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    sd = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    per_period = float(returns.mean()) / sd if sd > 0 else None
    # Evidence comes only from bars with capital at risk; long flat stretches must not count as data.
    active = returns[returns != 0]
    active_sd = float(active.std(ddof=1)) if len(active) > 1 else 0.0
    return {
        "active_bars": len(active),
        "sharpe_active": float(active.mean()) / active_sd if active_sd > 0 else None,
        "skew_active": _moment(active, 3) if len(active) > 2 else 0.0,
        "kurtosis_active": _moment(active, 4) if len(active) > 2 else 3.0,
        "return": float(equity[-1] - 1),
        "sharpe": per_period * math.sqrt(periods_per_year) if per_period is not None else None,
        "sharpe_per_bar": per_period,
        "max_drawdown": float(np.max(1 - equity / peak)),
        "volatility": sd * math.sqrt(periods_per_year),
        "bars": len(returns),
        "skew": _moment(returns, 3),
        "kurtosis": _moment(returns, 4),
    }


def _moment(x: np.ndarray, k: int) -> float:
    sd = x.std()
    return float(np.mean(((x - x.mean()) / sd) ** k)) if sd > 0 else (0.0 if k == 3 else 3.0)


def deflated_sharpe(sr: float, sr0: float, bars: int, skew: float, kurtosis: float) -> float:
    """Probability the true per-bar Sharpe exceeds `sr0`, the best Sharpe expected from luck alone."""
    denom = 1 - skew * sr + (kurtosis - 1) / 4 * sr * sr
    if bars < 3 or denom <= 0:
        return 0.0
    return NormalDist().cdf((sr - sr0) * math.sqrt(bars - 1) / math.sqrt(denom))


def expected_max_sharpe(trials: int, variance: float) -> float:
    if trials < 2 or variance <= 0:
        return 0.0
    z = NormalDist().inv_cdf
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * z(1 - 1 / trials) + EULER_GAMMA * z(1 - 1 / (trials * math.e))
    )


def _deflate(evaluations: list[Evaluation], trials: int) -> None:
    """Deflate each validation Sharpe for the number of strategies tried in the run.

    The luck benchmark is the Sharpe ratio the best of `trials` strategies with no skill at all would be
    expected to reach over the same number of bars: under that null each per-bar Sharpe estimate has a
    sampling variance of about 1/(bars-1). Only bars with a position count as observations.
    """
    for e in evaluations:
        v = e.validation
        bars = v.get("active_bars", 0)
        if v.get("sharpe_active") is None or bars < 3:
            e.dsr = None
            continue
        sr0 = expected_max_sharpe(trials, 1 / (bars - 1))
        e.dsr = deflated_sharpe(v["sharpe_active"], sr0, bars, v["skew_active"], v["kurtosis_active"])


def _apply_gates(e: Evaluation, config: ResearchConfig) -> None:
    v, h, reasons = e.validation, e.holdout, []
    if v["trades"] < config.min_trades:
        reasons.append(f"only {v['trades']} trades in validation (need {config.min_trades})")
    if v["sharpe"] is None or v["sharpe"] < config.min_sharpe:
        reasons.append(f"validation Sharpe {_fmt(v['sharpe'])} below {config.min_sharpe}")
    if v["max_drawdown"] is not None and v["max_drawdown"] > config.max_drawdown:
        reasons.append(f"drawdown {v['max_drawdown']:.0%} above {config.max_drawdown:.0%}")
    if v["positive_folds"] < min(config.min_positive_folds, config.folds - 1):
        reasons.append(f"profitable in only {v['positive_folds']} of {config.folds - 1} validation periods")
    if e.dsr is None or e.dsr < config.min_dsr:
        reasons.append(f"likely luck: deflated Sharpe confidence {_fmt(e.dsr)} below {config.min_dsr}")
    if config.require_positive_holdout and not (h["return"] is not None and h["return"] > 0):
        reasons.append(f"lost money in the unseen holdout period ({_fmt(h['return'], pct=True)})")
    e.reasons, e.passed = reasons, not reasons


def _select(evaluations: list[Evaluation], config: ResearchConfig) -> list[Selection]:
    best: dict[str, Evaluation] = {}
    for e in evaluations:
        if e.passed and (e.instrument_id not in best or e.score > best[e.instrument_id].score):
            best[e.instrument_id] = e
    chosen = sorted(best.values(), key=lambda e: e.score, reverse=True)[: config.max_positions]
    if not chosen:
        return []
    if len(chosen) == 1:
        weights = {chosen[0].instrument_id: 1.0}
    else:
        length = min(len(e.returns) for e in chosen)
        returns = {e.instrument_id: e.returns[-length:] for e in chosen}
        try:
            weights = optimize(returns, "RISK_PARITY", long_only=True, max_weight=1.0).weights
        except Exception:
            weights = {e.instrument_id: 1 / len(chosen) for e in chosen}
    selections = []
    for e in chosen:
        weight = min(weights.get(e.instrument_id, 0.0), config.max_weight)
        capital = (config.capital * Decimal(str(round(weight, 4)))).quantize(Decimal(1))
        selections.append(Selection(e, weight, capital))
    return selections


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}" if pct else f"{value:.2f}"
