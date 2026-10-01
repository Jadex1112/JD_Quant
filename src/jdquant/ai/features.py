"""Feature Store: one feature library for training and live inference (Chapter 63).

Online and offline values come from the same function applied to the same point-in-time window,
which removes training/serving skew by construction (AI-63004); tests verify parity anyway.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jdquant.core.errors import ValidationError
from jdquant.marketdata.records import Candle
from jdquant.strategy import indicators as ind

FeatureFn = Callable[[Sequence[Candle], dict[str, Any]], float | None]


def _closes(c: Sequence[Candle]) -> list[float]:
    return [float(x.close) for x in c]


def _ret(c, p):
    n = p.get("period", 1)
    if len(c) <= n:
        return None
    return float(c[-1].close) / float(c[-1 - n].close) - 1


def _log_ret(c, p):
    r = _ret(c, p)
    return None if r is None else math.log1p(r)


def _sma_ratio(c, p):
    value = ind.sma(_closes(c), p.get("period", 20))
    return None if not value else float(c[-1].close) / value - 1


def _ema_ratio(c, p):
    value = ind.ema(_closes(c), p.get("period", 20))
    return None if not value else float(c[-1].close) / value - 1


def _rsi(c, p):
    value = ind.rsi(_closes(c), p.get("period", 14))
    return None if value is None else value / 100


def _volatility(c, p):
    n = p.get("period", 20)
    closes = _closes(c)
    if len(closes) <= n:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / n
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))


def _zscore(c, p):
    return ind.zscore(_closes(c), p.get("period", 20))


def _atr_pct(c, p):
    n = p.get("period", 14)
    value = ind.atr([x.high for x in c], [x.low for x in c], [x.close for x in c], n)
    return None if value is None else value / float(c[-1].close)


def _range_pct(c, p):
    last = c[-1] if c else None
    return None if last is None else (float(last.high) - float(last.low)) / float(last.close)


def _volume_z(c, p):
    n = p.get("period", 20)
    vols = [float(x.volume) for x in c]
    return ind.zscore(vols, n)


LIBRARY: dict[str, tuple[FeatureFn, str]] = {
    "return": (_ret, "Simple return over `period` bars"),
    "log_return": (_log_ret, "Log return over `period` bars"),
    "sma_ratio": (_sma_ratio, "Close / SMA(period) - 1"),
    "ema_ratio": (_ema_ratio, "Close / EMA(period) - 1"),
    "rsi": (_rsi, "RSI(period) scaled to [0, 1]"),
    "volatility": (_volatility, "Std-dev of log returns over `period` bars"),
    "zscore": (_zscore, "Z-score of close over `period` bars"),
    "atr_pct": (_atr_pct, "ATR(period) / close"),
    "range_pct": (_range_pct, "(High - low) / close of the last bar"),
    "volume_zscore": (_volume_z, "Z-score of volume over `period` bars"),
}


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def compute(self, window: Sequence[Candle]) -> float | None:
        fn, _ = LIBRARY[self.kind]
        return fn(window, self.params)


@dataclass(frozen=True)
class FeatureSet:
    name: str
    version: int
    features: tuple[FeatureDefinition, ...]
    lookback: int = 100

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.features]

    def vector(self, window: Sequence[Candle]) -> list[float] | None:
        """Online value for the latest bar; None while any feature lacks history."""
        window = window[-self.lookback :]
        values = [f.compute(window) for f in self.features]
        return None if any(v is None or not math.isfinite(v) for v in values) else values

    def materialize(self, candles: Sequence[Candle]) -> list[tuple[datetime, list[float]]]:
        """Offline values; row t only sees candles closed at or before t (AI-63005)."""
        rows = []
        for i in range(len(candles)):
            vec = self.vector(candles[max(0, i + 1 - self.lookback) : i + 1])
            if vec is not None:
                rows.append((candles[i].close_ts, vec))
        return rows


def build_feature_set(
    name: str, spec: list[dict[str, Any]], version: int = 1, lookback: int = 100
) -> FeatureSet:
    violations = []
    features = []
    for i, item in enumerate(spec):
        kind = item.get("kind")
        if kind not in LIBRARY:
            violations.append({"field": f"features[{i}].kind", "message": f"one of {sorted(LIBRARY)}"})
            continue
        params = {k: v for k, v in item.items() if k not in ("kind", "name")}
        if "period" in params and (
            not isinstance(params["period"], int) or not 1 <= params["period"] < lookback
        ):
            violations.append({"field": f"features[{i}].period", "message": f"integer in [1, {lookback})"})
        features.append(
            FeatureDefinition(
                item.get("name") or f"{kind}_{params.get('period', '')}".rstrip("_"), kind, params
            )
        )
    if not features and not violations:
        violations.append({"field": "features", "message": "at least one feature is required"})
    if violations:
        raise ValidationError("FEATURE_SET_INVALID", violations)
    return FeatureSet(name, version, tuple(features), lookback)


DEFAULT_FEATURES = [
    {"kind": "return", "period": 1},
    {"kind": "return", "period": 5},
    {"kind": "sma_ratio", "period": 20},
    {"kind": "rsi", "period": 14},
    {"kind": "volatility", "period": 20},
    {"kind": "zscore", "period": 20},
]
