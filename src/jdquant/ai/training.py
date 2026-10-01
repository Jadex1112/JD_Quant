"""Training pipeline: point-in-time datasets, purged time splits, numpy models, evaluation (Chapter 58)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from jdquant.ai.features import FeatureSet
from jdquant.core.errors import ValidationError
from jdquant.marketdata.records import Candle

ALGORITHMS = ("logistic_regression", "ridge_regression")


@dataclass(frozen=True)
class TrainingConfig:
    algorithm: str = "logistic_regression"
    horizon: int = 1  # label: forward return over `horizon` bars
    test_fraction: float = 0.25
    embargo: int = 5
    l2: float = 1.0
    cost_bps: float = 5.0
    seed: int = 0


@dataclass
class Dataset:
    timestamps: list
    X: np.ndarray
    forward_returns: np.ndarray


def build_dataset(feature_set: FeatureSet, candles: list[Candle], horizon: int) -> Dataset:
    """Features at bar t, label from closes t → t+horizon: no label information at feature time."""
    closes = np.array([float(c.close) for c in candles])
    index = {c.close_ts: i for i, c in enumerate(candles)}
    ts, rows, fwd = [], [], []
    for at, vec in feature_set.materialize(candles):
        i = index[at]
        if i + horizon < len(candles):
            ts.append(at)
            rows.append(vec)
            fwd.append(closes[i + horizon] / closes[i] - 1)
    return Dataset(ts, np.array(rows, dtype=float), np.array(fwd, dtype=float))


def purged_split(n: int, test_fraction: float, horizon: int, embargo: int) -> tuple[np.ndarray, np.ndarray]:
    """Holdout at the end; drop `horizon + embargo` samples before the test set (AI-58003)."""
    test_start = int(n * (1 - test_fraction))
    train_end = max(0, test_start - horizon - embargo)
    return np.arange(0, train_end), np.arange(test_start, n)


# ---- models -----------------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float, iterations: int = 50) -> np.ndarray:
    """L2-regularized logistic regression by Newton's method (deterministic)."""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    reg = np.eye(Xb.shape[1]) * l2
    reg[0, 0] = 0
    for _ in range(iterations):
        p = _sigmoid(Xb @ w)
        grad = Xb.T @ (p - y) + reg @ w
        hess = Xb.T @ (Xb * (p * (1 - p))[:, None]) + reg + np.eye(len(w)) * 1e-9
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-10:
            break
    return w


def fit_ridge(X: np.ndarray, y: np.ndarray, l2: float) -> np.ndarray:
    Xb = np.hstack([np.ones((len(X), 1)), X])
    reg = np.eye(Xb.shape[1]) * l2
    reg[0, 0] = 0
    return np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)


@dataclass
class LinearModel:
    """Framework-neutral artifact: plain JSON weights and scaling (CON-028)."""

    algorithm: str
    feature_names: list[str]
    means: list[float]
    stds: list[float]
    weights: list[float]
    horizon: int

    def score(self, vector: list[float]) -> float:
        x = (np.array(vector) - np.array(self.means)) / np.array(self.stds)
        z = self.weights[0] + float(np.dot(self.weights[1:], x))
        return float(_sigmoid(np.array([z]))[0]) if self.algorithm == "logistic_regression" else z

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


# ---- metrics ----------------------------------------------------------------------------------


def auc(y: np.ndarray, score: np.ndarray) -> float | None:
    pos, neg = score[y == 1], score[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def _classification_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    pred = (p >= 0.5).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    eps = 1e-12
    return {
        "accuracy": float((pred == y).mean()),
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "auc": auc(y, p),
        "log_loss": float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))),
        "baseline_accuracy": float(max(y.mean(), 1 - y.mean())),
    }


def _regression_metrics(y: np.ndarray, pred: np.ndarray) -> dict[str, float | None]:
    resid = y - pred
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "mae": float(np.abs(resid).mean()),
        "rmse": float(np.sqrt((resid**2).mean())),
        "r2": 1 - float((resid**2).sum()) / ss_tot if ss_tot else None,
    }


def _trading_metrics(
    signal: np.ndarray, fwd: np.ndarray, horizon: int, cost_bps: float
) -> dict[str, float | None]:
    """Non-overlapping evaluation of a long/flat/short position taken from the prediction."""
    idx = np.arange(0, len(signal), horizon)
    pos, ret = signal[idx], fwd[idx]
    turnover = np.abs(np.diff(np.concatenate([[0.0], pos])))
    pnl = pos * ret - turnover * cost_bps / 10_000
    sd = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    return {
        "strategy_total_return": float(np.prod(1 + pnl) - 1),
        "strategy_sharpe_per_period": float(pnl.mean() / sd) if sd else None,
        "hit_rate": float((np.sign(pos) == np.sign(ret))[pos != 0].mean()) if (pos != 0).any() else None,
        "periods": int(len(pnl)),
    }


# ---- pipeline ---------------------------------------------------------------------------------


def train(feature_set: FeatureSet, candles: list[Candle], config: TrainingConfig) -> tuple[LinearModel, dict]:
    if config.algorithm not in ALGORITHMS:
        raise ValidationError(
            "ALGORITHM_UNKNOWN", [{"field": "algorithm", "message": f"one of {ALGORITHMS}"}]
        )
    data = build_dataset(feature_set, candles, config.horizon)
    if len(data.X) < 100:
        raise ValidationError(
            "DATASET_TOO_SMALL", [{"field": "candles", "message": "need at least 100 samples"}]
        )
    train_idx, test_idx = purged_split(len(data.X), config.test_fraction, config.horizon, config.embargo)
    X_train, X_test = data.X[train_idx], data.X[test_idx]
    means, stds = X_train.mean(axis=0), X_train.std(axis=0)
    stds[stds == 0] = 1.0
    Z_train, Z_test = (X_train - means) / stds, (X_test - means) / stds
    fwd_train, fwd_test = data.forward_returns[train_idx], data.forward_returns[test_idx]

    if config.algorithm == "logistic_regression":
        y_train, y_test = (fwd_train > 0).astype(int), (fwd_test > 0).astype(int)
        w = fit_logistic(Z_train, y_train, config.l2)
        p_train = _sigmoid(np.hstack([np.ones((len(Z_train), 1)), Z_train]) @ w)
        p_test = _sigmoid(np.hstack([np.ones((len(Z_test), 1)), Z_test]) @ w)
        metrics = {
            "train": _classification_metrics(y_train, p_train),
            "test": _classification_metrics(y_test, p_test),
        }
        signal_test = np.where(p_test > 0.5, 1.0, -1.0)
        score_test = p_test
    else:
        w = fit_ridge(Z_train, fwd_train, config.l2)
        pred_train = np.hstack([np.ones((len(Z_train), 1)), Z_train]) @ w
        pred_test = np.hstack([np.ones((len(Z_test), 1)), Z_test]) @ w
        metrics = {
            "train": _regression_metrics(fwd_train, pred_train),
            "test": _regression_metrics(fwd_test, pred_test),
        }
        signal_test = np.sign(pred_test)
        score_test = pred_test

    metrics["test"]["information_coefficient"] = spearman(score_test, fwd_test)
    metrics["test"].update(_trading_metrics(signal_test, fwd_test, config.horizon, config.cost_bps))

    model = LinearModel(
        config.algorithm, feature_set.names, means.tolist(), stds.tolist(), w.tolist(), config.horizon
    )
    report = {
        "config": dict(config.__dict__),
        "samples": {
            "train": int(len(train_idx)),
            "test": int(len(test_idx)),
            "purged": int(len(data.X) - len(train_idx) - len(test_idx)),
        },
        "period": {"start": data.timestamps[0].isoformat(), "end": data.timestamps[-1].isoformat()},
        "metrics": metrics,
        "feature_importance": dict(zip(feature_set.names, [abs(v) for v in w[1:].tolist()], strict=True)),
        "reference_distribution": _quantile_edges(X_train),
        "warnings": _warnings(metrics, config),
    }
    return model, report


def _quantile_edges(X: np.ndarray, bins: int = 10) -> dict[str, list[float]]:
    return {str(i): np.quantile(X[:, i], np.linspace(0, 1, bins + 1)).tolist() for i in range(X.shape[1])}


def _warnings(metrics: dict, config: TrainingConfig) -> list[str]:
    out = []
    test, train = metrics["test"], metrics["train"]
    if "accuracy" in test and test["accuracy"] is not None:
        if test["accuracy"] <= test["baseline_accuracy"]:
            out.append("test accuracy does not beat the majority-class baseline")
        if train["accuracy"] - test["accuracy"] > 0.1:
            out.append("large train/test accuracy gap: probable overfitting")
    if test.get("strategy_total_return") is not None and test["strategy_total_return"] <= 0:
        out.append(f"prediction-driven strategy loses money after {config.cost_bps} bps costs")
    return out


def psi(reference_edges: list[float], values: list[float]) -> float | None:
    """Population Stability Index of recent values against training decile edges (AI-57008)."""
    if len(values) < 20:
        return None
    edges = np.array(reference_edges, dtype=float)
    inner = edges[1:-1]
    actual = np.bincount(np.searchsorted(inner, values, side="right"), minlength=len(edges) - 1) / len(values)
    expected = np.full(len(edges) - 1, 1 / (len(edges) - 1))
    actual = np.clip(actual, 1e-6, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))
