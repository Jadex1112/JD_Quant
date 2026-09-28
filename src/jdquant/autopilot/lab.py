"""The strategy lab: type a strategy in plain words, the AI turns it into rules, the platform backtests it.

1. Translate: the AI writes the rules (`jdquant.strategy.rules`), listing every assumption it made and
   anything it could not express. The rules are validated, and repaired once by the AI if invalid, then
   shown back in plain English so the author can check them before anything runs.
2. Backtest: on broker history when a broker is connected (else clearly labelled synthetic data), with
   the market's charges, slippage and financing.
3. Confidence: the probability that the edge is real after allowing for how many variations the author
   has tried on this market (deflated Sharpe ratio), the probability of profit from resampling the trades,
   and the same checks the autopilot applies (enough trades, drawdown, consistency across periods, a
   profitable final period, charges). The AI then explains the result and suggests what to test next.
4. Paper trade: one click deploys the rules on a paper account, where they trade live prices.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np

from jdquant.ai.prompts import STRATEGY_BUILDER, STRATEGY_REVIEW
from jdquant.autopilot.research import (
    _stats,
    deflated_sharpe,
    expected_max_sharpe,
    fees_for,
    market_leverage,
    slippage_for,
)
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.markets.india import Product
from jdquant.markets.sessions import session_for
from jdquant.persistence.codec import decode, encode
from jdquant.strategy.rules import RuleStrategy, describe, rules_to_python, spec_lookback, validate_spec

KIND = "lab_run"
PERIODS = 4


@dataclass
class LabRun:
    run_id: str
    at: datetime
    user_id: str
    instrument_id: str
    interval_seconds: int
    bars: int
    spec: dict[str, Any]
    description: list[str]
    text: str = ""
    assumptions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    data_source: str = ""
    capital: str = ""  # budget in INR
    capital_quote: str = ""  # in the instrument's quote currency
    currency: str = ""
    leverage: str = "1"
    results: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    equity: list[tuple[str, float]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] | None = None
    model: str = ""
    deployment_id: str | None = None


class StrategyLab:
    def __init__(self, platform, store, autopilot, chat=None, runner=None):
        self._p, self._store, self._a = platform, store, autopilot
        self.chat, self._runner = chat, runner

    # ---- plain words -> rules -------------------------------------------------------------------

    def models(self) -> dict[str, Any]:
        """Which AI writes the rules, and the other models the same NVIDIA key can use."""
        from jdquant.ai.analyst import NVIDIA_PRESETS, OpenAICompatibleChat

        chat = self.chat
        if chat is None:
            return {"provider": None, "default": None, "presets": [], "available": [], "switchable": False}
        switchable = isinstance(chat, OpenAICompatibleChat)
        available, error = [], None
        if switchable:
            try:
                available = chat.list_models()
            except Exception as exc:  # the presets still work; the list is only a convenience
                error = f"could not list models: {str(exc)[:200]}"
        return {
            "provider": chat.provider,
            "default": chat.model,
            "presets": [{"id": m, "label": label} for m, label in NVIDIA_PRESETS] if switchable else [],
            "available": available,
            "switchable": switchable,
            "error": error,
        }

    def translate(
        self, text: str, instrument_id: str, interval_seconds: int, model: str | None = None
    ) -> dict[str, Any]:
        from jdquant.ai.analyst import MODEL_ID, OpenAICompatibleChat

        if self.chat is None:
            raise PlatformError(
                "AI_UNAVAILABLE",
                "set NVIDIA_API_KEY (or an Anthropic key) on the server to translate strategies",
            )
        chat = self.chat
        if model:
            if not isinstance(chat, OpenAICompatibleChat):
                raise PlatformError(
                    "MODEL_NOT_SWITCHABLE",
                    f"{chat.provider} is configured; set NVIDIA_API_KEY to choose models",
                )
            if not MODEL_ID.match(model) or len(model) > 120:
                raise ValidationError(
                    "MODEL_INVALID", [{"field": "model", "message": "a model id like moonshotai/kimi-k3"}]
                )
            chat = chat.with_model(model)
        instrument = self._p.instruments.get(instrument_id)
        payload: dict[str, Any] = {
            "text": text[:4000],
            "instrument": {
                "id": instrument_id,
                "base": instrument.base_asset,
                "quote": instrument.quote_asset,
                "market": session_for(instrument).name,
                "can_short": instrument.can_short,
            },
            "bar_seconds": interval_seconds,
        }
        problems: list[dict[str, str]] = []
        for _ in range(2):  # one repair attempt when the rules come back invalid
            answer = _json(chat.ask(STRATEGY_BUILDER, payload, max_tokens=16384, timeout=300))
            if answer is None:
                raise PlatformError(
                    "AI_NO_ANSWER", f"{chat.provider} {chat.model} did not return rules; try again"
                )
            try:
                spec = validate_spec(answer.get("spec"))
            except ValidationError as exc:
                problems = exc.details if isinstance(exc.details, list) else [{"message": str(exc)}]
                payload.update(previous=answer.get("spec"), problems=problems)
                continue
            return {
                "spec": spec,
                "description": describe(spec),
                "assumptions": [str(x)[:300] for x in answer.get("assumptions") or []][:12],
                "unsupported": [str(x)[:300] for x in answer.get("unsupported") or []][:12],
                "model": f"{chat.provider} · {chat.model}",
                "code": rules_to_python(spec),
            }
        raise ValidationError("RULES_INVALID", problems)

    # ---- backtest ---------------------------------------------------------------------------------

    def backtest(
        self,
        spec: Any,
        instrument_id: str,
        *,
        interval_seconds: int,
        bars: int,
        capital: Decimal,
        leverage: Decimal,
        user_id: str,
        text: str = "",
        assumptions: list[str] | None = None,
        unsupported: list[str] | None = None,
    ) -> LabRun:
        spec = validate_spec(spec)
        instrument = self._p.instruments.get(instrument_id)
        warmup = spec_lookback(spec)
        candles, source = self._history(instrument, interval_seconds, bars + warmup)
        if len(candles) < warmup + 50:
            raise PlatformError(
                "DATA_UNAVAILABLE", f"only {len(candles)} bars of history for {instrument_id}"
            )
        rates = self._a.fx_rates()
        rate = rates.get(instrument.quote_asset)
        if not rate:
            raise PlatformError(
                "FX_RATE_MISSING", f"set a {instrument.quote_asset} rate in the autopilot settings"
            )
        capital_quote = (capital / rate).quantize(Decimal("0.01"))
        cap, _ = market_leverage(instrument, bool(spec.get("flat_at_cutoff")))
        effective = max(Decimal(1), min(leverage, cap))
        session = session_for(instrument)
        ppy = session.bars_per_year(interval_seconds)
        trade_after = candles[warmup - 1].close_ts
        params = {
            "spec": json.dumps(spec),
            "capital": str(capital_quote),
            "leverage": str(effective),
            "allow_short": True,
            "trade_after": trade_after.isoformat(),
        }
        config = self._a.config.research_config()
        result = run_backtest(
            BacktestConfig(
                strategy=RuleStrategy,
                instruments=[instrument],
                candles={instrument_id: candles},
                parameters=params,
                initial_capital=capital_quote,
                base_currency=instrument.quote_asset,
                fees=fees_for(instrument, Product.INTRADAY if spec.get("flat_at_cutoff") else Product.CNC),
                slippage_bps=slippage_for(instrument, config),
                periods_per_year=ppy,
            )
        )
        curve = [(t, float(v)) for t, v in result.equity_curve if t >= trade_after]
        values = np.array([v for _, v in curve])
        returns = np.diff(values) / values[:-1] if len(values) > 1 else np.array([])
        stats = _stats(returns, ppy)
        pnls = [float(t.net_pnl) for t in result.trades]
        fees = float(sum((f.fee for f in result.fills), result.financing))
        gross = float(values[-1] - values[0]) + fees if len(values) else 0.0
        tested = candles[warmup - 1 :]
        benchmark = float(tested[-1].close / tested[0].close - 1) if tested else None
        chunks = np.array_split(values, PERIODS) if len(values) >= PERIODS * 2 else []
        periods = [float(c[-1] / c[0] - 1) for c in chunks if len(c) > 1]
        trials = 1 + sum(
            1
            for d in self._store.all(KIND)
            if d.get("user_id") == user_id and d.get("instrument_id") == instrument_id
        )
        results = {
            "return": stats["return"],
            "sharpe": stats["sharpe"],
            "max_drawdown": stats["max_drawdown"],
            "volatility": stats.get("volatility"),
            "trades": len(result.trades),
            "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None,
            "profit_factor": (
                sum(p for p in pnls if p > 0) / -sum(p for p in pnls if p < 0)
                if any(p < 0 for p in pnls)
                else None
            ),
            "benchmark_return": benchmark,
            "charges": fees,
            "charges_share": fees / gross if gross > 0 else None,
            "periods": periods,
            "bars": len(returns),
            "leverage_used": str(effective),
            "leverage_cap": str(cap),
            "strategy_errors": result.strategy_errors,
        }
        confidence = _confidence(stats, results, pnls, trials, synthetic=source != "broker history")
        run = LabRun(
            run_id=uuid.uuid4().hex[:12],
            at=self._p.clock.now(),
            user_id=user_id,
            instrument_id=instrument_id,
            interval_seconds=interval_seconds,
            bars=len(returns),
            spec=spec,
            description=describe(spec),
            text=text[:4000],
            assumptions=list(assumptions or []),
            unsupported=list(unsupported or []),
            data_source=source,
            capital=str(capital),
            capital_quote=str(capital_quote),
            currency=instrument.quote_asset,
            leverage=str(effective),
            results=results,
            confidence=confidence,
            equity=[(t.isoformat(), v / values[0]) for t, v in _downsample(curve, 300)]
            if len(values)
            else [],
            trades=[
                {
                    "direction": t.direction.value,
                    "quantity": str(t.quantity),
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price),
                    "net_pnl": str(t.net_pnl),
                }
                for t in result.trades[-100:]
            ],
        )
        run.review, run.model = self._review(run)
        self._store.put(KIND, run.run_id, encode(run))
        return run

    def _history(self, instrument, interval_seconds: int, bars: int):
        loader = self._a._venue_candles
        if loader is not None:
            try:
                series = loader(instrument, interval_seconds, bars)
            except Exception:  # a broker without that bar size: fall back to demo data, labelled
                series = None
            if series:
                return series, "broker history"
        return self._a._synthetic(instrument, interval_seconds, bars), "synthetic demo data"

    def _review(self, run: LabRun) -> tuple[dict[str, Any] | None, str]:
        if self.chat is None:
            return None, ""
        briefing = {
            "rules": run.description,
            "instrument": run.instrument_id,
            "bar_seconds": run.interval_seconds,
            "data": run.data_source,
            "results": run.results,
            "confidence": run.confidence,
        }
        answer = _json(self.chat.ask(STRATEGY_REVIEW, briefing, max_tokens=4096))
        if answer is None:
            return None, ""
        review = {
            "summary": str(answer.get("summary") or "")[:1200],
            "strengths": [str(x)[:300] for x in answer.get("strengths") or []][:5],
            "weaknesses": [str(x)[:300] for x in answer.get("weaknesses") or []][:5],
            "suggestions": [str(x)[:300] for x in answer.get("suggestions") or []][:3],
        }
        return review, f"{self.chat.provider} · {self.chat.model}"

    # ---- history and paper trading ----------------------------------------------------------------

    def runs(self, user_id: str | None = None, limit: int = 50) -> list[LabRun]:
        runs = [decode(LabRun, d) for d in self._store.all(KIND)]
        runs = [r for r in runs if user_id is None or r.user_id == user_id]
        return sorted(runs, key=lambda r: r.at, reverse=True)[:limit]

    def get(self, run_id: str) -> LabRun:
        doc = self._store.get(KIND, run_id)
        if doc is None:
            from jdquant.core.errors import NotFoundError

            raise NotFoundError("LAB_RUN_NOT_FOUND", f"unknown lab run {run_id}")
        return decode(LabRun, doc)

    def paper_trade(self, run_id: str, account_id: str, user_id: str):
        """Deploy the tested rules on a paper account, where they trade live prices."""
        from jdquant.trading.engine import AccountMode

        run = self.get(run_id)
        trading = self._p.trading
        if trading.get_account(account_id).mode is not AccountMode.PAPER:
            raise PlatformError("PAPER_ONLY", "lab strategies go to a paper account first")
        deployment = trading.create_deployment(
            strategy_name=RuleStrategy.name,
            strategy_version=RuleStrategy.version,
            account_id=account_id,
            parameters={
                "spec": json.dumps(run.spec),
                "capital": run.capital_quote,
                "leverage": run.leverage,
                "allow_short": True,
                "trade_after": "",
            },
            instruments=[run.instrument_id],
            created_by=user_id,
            bar_interval_seconds=run.interval_seconds,
        )
        trading.approve(deployment.deployment_id, user_id)
        trading.start(deployment.deployment_id)
        if self._runner is not None:
            self._runner.sync(deployment)
        run.deployment_id = deployment.deployment_id
        self._store.put(KIND, run.run_id, encode(run))
        return deployment


def _confidence(
    stats: dict, results: dict, pnls: list[float], trials: int, *, synthetic: bool
) -> dict[str, Any]:
    bars = stats.get("active_bars", 0)
    dsr = None
    if stats.get("sharpe_active") is not None and bars >= 3:
        sr0 = expected_max_sharpe(trials, 1 / (bars - 1))
        dsr = deflated_sharpe(
            stats["sharpe_active"], sr0, bars, stats["skew_active"], stats["kurtosis_active"]
        )
    probability = None
    if len(pnls) >= 5:
        rng = np.random.default_rng(0)
        sample = rng.choice(np.array(pnls), size=(2000, len(pnls)), replace=True).sum(axis=1)
        probability = float((sample > 0).mean())
    periods = results["periods"]
    checks = [
        ("Enough trades", results["trades"] >= 10, f"{results['trades']} trades (10 or more wanted)"),
        (
            "Drawdown under 25%",
            (results["max_drawdown"] or 0) <= 0.25,
            f"worst fall {(results['max_drawdown'] or 0):.1%}",
        ),
        (
            "Profitable in most periods",
            sum(1 for p in periods if p > 0) >= 3,
            f"{sum(1 for p in periods if p > 0)} of {len(periods)} periods profitable",
        ),
        (
            "Profitable in the last period",
            bool(periods) and periods[-1] > 0,
            "the most recent quarter of the test",
        ),
        (
            "Charges under half the gross profit",
            results["charges_share"] is not None and results["charges_share"] <= 0.5,
            "charges share "
            + (
                "n/a (no gross profit)"
                if results["charges_share"] is None
                else f"{results['charges_share']:.0%}"
            ),
        ),
        (
            "Beat buying and holding",
            results["benchmark_return"] is None or (results["return"] or 0) > results["benchmark_return"],
            f"strategy {(results['return'] or 0):.1%} vs "
            f"buy and hold {(results['benchmark_return'] or 0):.1%}",
        ),
    ]
    failed = sum(1 for _, ok, _ in checks[:5] if not ok)
    score = round((dsr or 0) * 100)
    if synthetic:
        grade = "Low"
    elif score >= 90 and failed == 0:
        grade = "High"
    elif score >= 75 and failed <= 1:
        grade = "Medium"
    else:
        grade = "Low"
    return {
        "score": score,
        "grade": grade,
        "dsr": dsr,
        "trials": trials,
        "probability_of_profit": probability,
        "synthetic": synthetic,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }


def _json(text: str | None) -> dict | None:
    if text is None:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _downsample(points: list, limit: int) -> list:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(k * step)] for k in range(limit)] + [points[-1]]
