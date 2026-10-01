"""Candlestick forecasts from Kronos, and a scorecard of how they turned out.

Kronos (https://github.com/shiyu-coder/Kronos, MIT) reads recent candles and samples possible future
candles. We draw several independent paths and report the spread of outcomes: the chance the price is
higher after the horizon, the median expected move, and a 10–90% band. A single average path hides how
uncertain the model is, so it is not used on its own.

Every forecast is kept and **scored forward**: once the horizon has passed, the actual close is compared
with the forecast (was the direction right, was the price inside the band, Brier score of the probability).
That scorecard is the evidence for whether Kronos helps on a market. A backtest cannot be: Kronos was
pre-trained on history from 45 exchanges up to its 2025 release, so it may already have seen the test
period.

PyTorch and the model weights are optional (`pip install "jdquant[forecast]"`); the weights download from
Hugging Face on first use. Without them the page says so and nothing else is affected.
"""

from __future__ import annotations

import hashlib
import logging
import statistics
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.records import Candle

log = logging.getLogger(__name__)
KIND = "forecast"
MODELS = {
    "kronos-mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "kronos-small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
    "kronos-base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
}
CAVEAT = (
    "Kronos was pre-trained on market history up to its 2025 release, so it may have seen past periods: "
    "judge it by the forward scorecard, not by backtests. A forecast is a probability, not a promise."
)


class PathPredictor(Protocol):
    name: str

    def paths(self, history: list[Candle], horizon: int, count: int, seed: int) -> list[list[float]]:
        """`count` sampled paths of future closes, each `horizon` bars long."""


def forecast_dependencies() -> str | None:
    """None when PyTorch and friends import; otherwise what is missing."""
    missing = []
    for module in ("torch", "pandas", "einops", "huggingface_hub", "safetensors"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    return None if not missing else f'missing {", ".join(missing)}: pip install "jdquant[forecast]"'


class KronosPredictor:
    """Loads a Kronos model from Hugging Face once, then samples independent paths."""

    def __init__(self, model: str = "kronos-small", device: str | None = None):
        if model not in MODELS:
            raise ValidationError(
                "MODEL_UNKNOWN", [{"field": "model", "message": f"one of {sorted(MODELS)}"}]
            )
        self.name = model
        self._device = device
        self._predictor = None
        self._lock = threading.Lock()

    def _load(self):
        if self._predictor is None:
            problem = forecast_dependencies()
            if problem:
                raise PlatformError("FORECAST_UNAVAILABLE", problem)
            from jdquant.forecast.kronos import Kronos, KronosTokenizer
            from jdquant.forecast.kronos import KronosPredictor as Inner

            repo, tokenizer_repo, context = MODELS[self.name]
            try:
                tokenizer = KronosTokenizer.from_pretrained(tokenizer_repo)
                model = Kronos.from_pretrained(repo)
            except Exception as exc:
                raise PlatformError(
                    "FORECAST_MODEL_DOWNLOAD", f"could not load {repo} from Hugging Face: {str(exc)[:200]}"
                ) from None
            self._predictor = Inner(model, tokenizer, device=self._device, max_context=context)
        return self._predictor

    def paths(self, history: list[Candle], horizon: int, count: int, seed: int) -> list[list[float]]:
        import pandas as pd
        import torch

        with self._lock:
            predictor = self._load()
            interval = history[-1].close_ts - history[-1].open_ts
            frame = pd.DataFrame(
                {
                    "open": [float(c.open) for c in history],
                    "high": [float(c.high) for c in history],
                    "low": [float(c.low) for c in history],
                    "close": [float(c.close) for c in history],
                    "volume": [float(c.volume) for c in history],
                }
            )
            frame["amount"] = frame["volume"] * frame[["open", "high", "low", "close"]].mean(axis=1)
            x_ts = pd.Series(pd.to_datetime([c.open_ts for c in history]))
            y_ts = pd.Series(
                pd.to_datetime([history[-1].open_ts + interval * (k + 1) for k in range(horizon)])
            )
            out = []
            for i in range(count):
                torch.manual_seed(
                    seed + i
                )  # reproducible, independent paths (the model averages its own samples)
                predicted = predictor.predict(
                    df=frame,
                    x_timestamp=x_ts,
                    y_timestamp=y_ts,
                    pred_len=horizon,
                    T=1.0,
                    top_p=0.9,
                    sample_count=1,
                    verbose=False,
                )
                out.append([float(v) for v in predicted["close"].tolist()])
            return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def summarize(last_close: float, paths: list[list[float]]) -> dict[str, Any]:
    """The distribution of sampled outcomes: probability up, median move, 10–90% band per step."""
    finals = [p[-1] for p in paths]
    steps = len(paths[0])
    band = [
        {
            "step": k + 1,
            "p10": _quantile([p[k] for p in paths], 0.1),
            "p50": _quantile([p[k] for p in paths], 0.5),
            "p90": _quantile([p[k] for p in paths], 0.9),
        }
        for k in range(steps)
    ]
    returns = [(f / last_close - 1) * 100 for f in finals]
    return {
        "prob_up": sum(f > last_close for f in finals) / len(finals),
        "median_return_pct": statistics.median(returns),
        "mean_return_pct": statistics.fmean(returns),
        "p10": _quantile(finals, 0.1),
        "p50": _quantile(finals, 0.5),
        "p90": _quantile(finals, 0.9),
        "band": band,
    }


class ForecastService:
    def __init__(
        self,
        platform,
        store,
        *,
        history: Callable[[Any, int, int], tuple[list[Candle], str]],
        predictor_factory: Callable[[str], PathPredictor] | None = None,
    ):
        self._p = platform
        self._store = store
        self._history = history  # (instrument, interval_seconds, bars) -> (candles, source label)
        self._custom = predictor_factory is not None  # a supplied predictor needs no PyTorch here
        self._factory = predictor_factory or (lambda model: KronosPredictor(model))
        self._predictors: dict[str, PathPredictor] = {}
        self._lock = threading.Lock()
        saved = store.get(KIND, "settings") or {}
        self.model = saved.get("model", "kronos-small")
        self.paths_count = int(saved.get("paths", 16))

    # ---- settings -------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        problem = None if self._custom else forecast_dependencies()
        return {
            "available": problem is None,
            "problem": problem,
            "model": self.model,
            "models": sorted(MODELS),
            "paths": self.paths_count,
            "caveat": CAVEAT,
        }

    def configure(self, model: str | None = None, paths: int | None = None) -> dict[str, Any]:
        if model is not None:
            if model not in MODELS:
                raise ValidationError(
                    "MODEL_UNKNOWN", [{"field": "model", "message": f"one of {sorted(MODELS)}"}]
                )
            self.model = model
        if paths is not None:
            if not 4 <= paths <= 64:
                raise ValidationError("PATHS_INVALID", [{"field": "paths", "message": "between 4 and 64"}])
            self.paths_count = paths
        self._store.put(KIND, "settings", {"model": self.model, "paths": self.paths_count})
        return self.status()

    def _predictor(self) -> PathPredictor:
        with self._lock:
            if self.model not in self._predictors:
                self._predictors[self.model] = self._factory(self.model)
            return self._predictors[self.model]

    # ---- forecasting ----------------------------------------------------------------------------------

    def forecast(
        self,
        instrument_id: str,
        *,
        interval_seconds: int = 3600,
        horizon: int = 12,
        lookback: int = 400,
        record: bool = True,
    ) -> dict[str, Any]:
        if not 1 <= horizon <= 120:
            raise ValidationError("HORIZON_INVALID", [{"field": "horizon", "message": "1 to 120 bars"}])
        instrument = self._p.instruments.get(instrument_id)
        candles, source = self._history(instrument, interval_seconds, lookback)
        return self.forecast_candles(
            instrument_id,
            candles[-lookback:],
            interval_seconds=interval_seconds,
            horizon=horizon,
            source=source,
            record=record,
        )

    def forecast_candles(
        self,
        instrument_id: str,
        candles: list[Candle],
        *,
        interval_seconds: int,
        horizon: int,
        source: str = "live bars",
        record: bool = True,
    ) -> dict[str, Any]:
        """Forecast from the given closed candles (used by the Kronos strategy with its own bar history)."""
        if len(candles) < 60:
            raise PlatformError(
                "HISTORY_UNAVAILABLE", f"need at least 60 bars of history, have {len(candles)}"
            )
        last = candles[-1]
        key = f"{instrument_id}|{interval_seconds}|{last.close_ts.isoformat()}|{horizon}|{self.model}"
        seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        predictor = self._predictor()
        try:
            paths = predictor.paths(candles, horizon, self.paths_count, seed)
        except PlatformError:
            raise
        except Exception as exc:
            log.exception("forecast failed")
            raise PlatformError("FORECAST_FAILED", str(exc)[:300]) from None
        last_close = float(last.close)
        summary = summarize(last_close, paths)
        step = timedelta(seconds=interval_seconds)
        result = {
            "forecast_id": "F-" + hashlib.sha256((key + str(self._p.clock.now())).encode()).hexdigest()[:12],
            "instrument_id": instrument_id,
            "interval_seconds": interval_seconds,
            "horizon": horizon,
            "model": getattr(predictor, "name", self.model),
            "made_at": self._p.clock.now().isoformat(),
            "history_source": source,
            "last_close": last_close,
            "last_bar_close": last.close_ts.isoformat(),
            "target_time": (last.close_ts + step * horizon).isoformat(),
            "paths_count": len(paths),
            **summary,
            "band": [{**b, "time": (last.close_ts + step * b["step"]).isoformat()} for b in summary["band"]],
            "history": [
                {"time": c.open_ts.isoformat(), "close": float(c.close)}
                for c in candles[-max(60, horizon * 4) :]
            ],
            "status": "PENDING",
            "outcome": None,
            "caveat": CAVEAT,
        }
        if record and "synthetic" not in source:
            self._store.put(KIND, result["forecast_id"], {k: v for k, v in result.items() if k != "history"})
        return result

    # ---- the forward scorecard ------------------------------------------------------------------------

    def list(self, instrument_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = [
            d
            for d in self._store.all(KIND)
            if "forecast_id" in d and (instrument_id is None or d["instrument_id"] == instrument_id)
        ]
        return sorted(items, key=lambda d: d["made_at"], reverse=True)[:limit]

    def score_due(self) -> int:
        """Score every forecast whose horizon has passed, against the bar that closed at the target time."""
        now = self._p.clock.now()
        scored = 0
        for doc in self.list(limit=10_000):
            if doc["status"] != "PENDING":
                continue
            target = datetime.fromisoformat(doc["target_time"])
            if now < target:
                continue
            try:
                instrument = self._p.instruments.get(doc["instrument_id"])
                bars = int((now - target).total_seconds() // doc["interval_seconds"]) + doc["horizon"] + 5
                candles, _ = self._history(instrument, doc["interval_seconds"], min(bars, 2000))
            except Exception:
                continue
            actual = next((c for c in candles if c.close_ts == target), None)
            if actual is None:
                if now - target > timedelta(seconds=doc["interval_seconds"] * doc["horizon"] * 3):
                    doc["status"] = "UNSCORABLE"
                    self._store.put(KIND, doc["forecast_id"], doc)
                continue
            close = float(actual.close)
            up = close > doc["last_close"]
            doc["outcome"] = {
                "actual_close": close,
                "return_pct": (close / doc["last_close"] - 1) * 100,
                "direction_hit": None if doc["prob_up"] == 0.5 else (doc["prob_up"] > 0.5) == up,
                "inside_band": doc["p10"] <= close <= doc["p90"],
                "brier": (doc["prob_up"] - (1.0 if up else 0.0)) ** 2,
            }
            doc["status"] = "SCORED"
            self._store.put(KIND, doc["forecast_id"], doc)
            scored += 1
        return scored

    def scorecard(self, instrument_id: str | None = None) -> dict[str, Any]:
        done = [d for d in self.list(instrument_id, limit=10_000) if d["status"] == "SCORED"]
        directional = [d for d in done if d["outcome"]["direction_hit"] is not None]
        pending = sum(1 for d in self.list(instrument_id, limit=10_000) if d["status"] == "PENDING")
        if not done:
            return {"scored": 0, "pending": pending, "verdict": "No forecast has reached its horizon yet."}
        hits = sum(d["outcome"]["direction_hit"] for d in directional)
        hit_rate = hits / len(directional) if directional else None
        brier = statistics.fmean(d["outcome"]["brier"] for d in done)
        coverage = statistics.fmean(1.0 if d["outcome"]["inside_band"] else 0.0 for d in done)
        # a coin flip scores a Brier of 0.25; with fewer than 30 forecasts no verdict is possible
        if len(done) < 30:
            verdict = f"{len(done)} forecasts scored: too few to judge (30 or more needed)."
        elif hit_rate is not None and hit_rate > 0.55 and brier < 0.24:
            verdict = "Better than a coin flip so far on direction and calibration."
        else:
            verdict = "Not better than a coin flip so far: do not trade on it."
        return {
            "scored": len(done),
            "pending": pending,
            "hit_rate": hit_rate,
            "brier": brier,
            "band_coverage": coverage,
            "mean_abs_error_pct": statistics.fmean(
                abs(d["outcome"]["return_pct"] - d["median_return_pct"]) for d in done
            ),
            "verdict": verdict,
        }
