"""Portfolio Optimizer (Chapter 55): allocations from return histories under simple constraints."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jdquant.core.errors import PlatformError, ValidationError

METHODS = ("EQUAL_WEIGHT", "INVERSE_VOLATILITY", "MIN_VARIANCE", "MAX_SHARPE", "RISK_PARITY")


@dataclass
class OptimizationResult:
    method: str
    weights: dict[str, float]
    expected_return: float
    expected_volatility: float
    sharpe: float | None
    risk_contributions: dict[str, float]


def _project(w: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Euclidean projection onto {sum w = 1, lo <= w <= hi} by bisection on the shift."""
    a, b = -1.0 - hi - float(np.max(np.abs(w))), 1.0 + float(np.max(np.abs(w)))
    for _ in range(200):
        mid = (a + b) / 2
        if np.clip(w - mid, lo, hi).sum() > 1:
            a = mid
        else:
            b = mid
    return np.clip(w - (a + b) / 2, lo, hi)


def _solve(objective_grad, n: int, lo: float, hi: float, steps: int = 5000, lr: float = 0.5) -> np.ndarray:
    w = _project(np.full(n, 1 / n), lo, hi)
    for _ in range(steps):
        new = _project(w - lr * objective_grad(w), lo, hi)
        if np.max(np.abs(new - w)) < 1e-12:
            break
        w = new
    return w


def optimize(
    returns: dict[str, list[float]],
    method: str,
    *,
    long_only: bool = True,
    max_weight: float = 1.0,
    periods_per_year: float = 365,
    risk_free: float = 0.0,
) -> OptimizationResult:
    if method not in METHODS:
        raise ValidationError("METHOD_UNKNOWN", [{"field": "method", "message": f"one of {METHODS}"}])
    names = sorted(returns)
    n = len(names)
    if n < 2:
        raise ValidationError("UNIVERSE_TOO_SMALL", [{"field": "instruments", "message": "at least two"}])
    lengths = {len(returns[k]) for k in names}
    if len(lengths) != 1 or lengths.pop() < 30:
        raise ValidationError("HISTORY_INVALID", [{"field": "returns", "message": "equal lengths of >= 30"}])
    lo = 0.0 if long_only else -max_weight
    if long_only and max_weight * n < 1 - 1e-12:
        raise PlatformError(
            "OPTIMIZATION_INFEASIBLE",
            f"long-only weights capped at {max_weight} cannot sum to 1 across {n} assets",
        )
    R = np.array([returns[k] for k in names]).T
    mu = R.mean(axis=0) * periods_per_year
    cov = np.cov(R, rowvar=False) * periods_per_year + np.eye(n) * 1e-12
    scale = 1 / float(np.trace(cov) / n)

    if method == "EQUAL_WEIGHT":
        w = _project(np.full(n, 1 / n), lo, max_weight)
    elif method == "INVERSE_VOLATILITY":
        inv = 1 / np.sqrt(np.diag(cov))
        w = _project(inv / inv.sum(), lo, max_weight)
    elif method == "MIN_VARIANCE":
        w = _solve(lambda w: 2 * scale * cov @ w, n, lo, max_weight)
    elif method == "MAX_SHARPE":

        def grad(w):
            ret, var = w @ mu - risk_free, w @ cov @ w
            sd = np.sqrt(var)
            return -(mu * sd - ret * (cov @ w) / sd) / var

        w = _solve(grad, n, lo, max_weight, lr=0.05)
    else:  # RISK_PARITY: equal risk contribution by fixed-point iteration
        w = np.full(n, 1 / n)
        for _ in range(10_000):
            marginal = cov @ w
            target = (w @ marginal) / n
            new = w * (target / np.maximum(w * marginal, 1e-18)) ** 0.5
            new = _project(new / new.sum(), lo, max_weight)
            if np.max(np.abs(new - w)) < 1e-12:
                break
            w = new

    var = float(w @ cov @ w)
    vol = float(np.sqrt(var))
    ret = float(w @ mu)
    contributions = w * (cov @ w) / var if var else np.zeros(n)
    return OptimizationResult(
        method=method,
        weights=dict(zip(names, np.round(w, 10).tolist(), strict=True)),
        expected_return=ret,
        expected_volatility=vol,
        sharpe=(ret - risk_free) / vol if vol else None,
        risk_contributions=dict(zip(names, contributions.tolist(), strict=True)),
    )
