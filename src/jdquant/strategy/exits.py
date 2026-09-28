"""Exit engine: independent exit rules for every position a strategy opens.

A strategy attaches an `ExitPlan` when it enters. On every bar the plan is checked with the bar's high
and low, so a stop or target touched inside the bar counts:

- stop loss and target (prices);
- trailing stop (percent below the best price since entry, or above for shorts);
- break-even: once price has moved a set distance in favour, the stop moves to the entry price;
- time exit: close after a number of minutes;
- volatility exit: close if a single bar's range exceeds a multiple of the range at entry.

The first rule that fires closes the position, and its name becomes the exit reason in the signal log
and the trade journal. Signal reversals stay with the strategy, which knows its own signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ExitPlan:
    direction: int  # +1 long, -1 short
    entry: float
    opened_at: datetime
    stop: float | None = None
    target: float | None = None
    trail_pct: float | None = None
    breakeven_after: float | None = None  # price distance in favour that moves the stop to entry
    time_limit_minutes: float | None = None
    volatility_multiple: float | None = None
    entry_range: float | None = None
    best: float | None = None

    def __post_init__(self) -> None:
        self.best = self.entry if self.best is None else self.best


class ExitManager:
    def __init__(self) -> None:
        self.plans: dict[str, ExitPlan] = {}

    def attach(self, instrument_id: str, plan: ExitPlan) -> None:
        self.plans[instrument_id] = plan

    def clear(self, instrument_id: str) -> None:
        self.plans.pop(instrument_id, None)

    def plan(self, instrument_id: str) -> ExitPlan | None:
        return self.plans.get(instrument_id)

    def check(self, instrument_id: str, *, high: float, low: float, close: float, at: datetime) -> str | None:
        """The exit rule that fires on this bar, or None."""
        plan = self.plans.get(instrument_id)
        if plan is None:
            return None
        d = plan.direction
        worst, best_now = (low, high) if d > 0 else (high, low)
        if plan.stop is not None and (worst <= plan.stop if d > 0 else worst >= plan.stop):
            return "BREAK_EVEN_STOP" if plan.stop == plan.entry else "STOP_LOSS"
        if plan.target is not None and (best_now >= plan.target if d > 0 else best_now <= plan.target):
            return "TARGET"
        plan.best = max(plan.best, high) if d > 0 else min(plan.best, low)
        if plan.trail_pct:
            trail = (
                plan.best * (1 - plan.trail_pct / 100) if d > 0 else plan.best * (1 + plan.trail_pct / 100)
            )
            if (close <= trail) if d > 0 else (close >= trail):
                return "TRAILING_STOP"
        if plan.breakeven_after is not None and (plan.best - plan.entry) * d >= plan.breakeven_after:
            if plan.stop is None or (plan.stop < plan.entry if d > 0 else plan.stop > plan.entry):
                plan.stop = plan.entry
        if plan.time_limit_minutes is not None and at - plan.opened_at >= timedelta(
            minutes=plan.time_limit_minutes
        ):
            return "TIME_EXIT"
        if (
            plan.volatility_multiple
            and plan.entry_range
            and high - low >= plan.volatility_multiple * plan.entry_range
        ):
            return "VOLATILITY_EXIT"
        return None
