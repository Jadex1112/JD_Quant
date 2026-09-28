"""Strategies built on market structure and order flow.

- **VWAP reclaim** trades bars (so it can be backtested on history): enter when price closes back
  across the session VWAP on above-average volume, exit on the opposite cross or the exit plan.
- **Order-flow momentum** and **Liquidity-wall bounce** read live features (delta, aggression,
  imbalance, walls, absorption). History has no order books, so they are tested by replaying recorded
  sessions (`intelligence.replay.replay_backtest`), then paper traded.

Each decision is a pure function of the features and the position, used identically live and in
replay; every entry and exit carries reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jdquant.marketdata.records import Candle
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.exits import ExitPlan


@dataclass
class Decision:
    target: int  # desired direction: +1 long, 0 flat, -1 short
    reasons: list[str]
    confidence: float = 0.0
    stop: float | None = None
    take: float | None = None


def order_flow_decision(f: dict[str, Any], position: int, p: dict[str, Any]) -> Decision | None:
    """Momentum with the flow: buyers in control, book leaning their way, above VWAP, busy tape."""
    if not f or f.get("price") is None:
        return None
    price, tick = f["price"], f["tick"]
    ratio = f.get("aggressive_ratio_5m")
    imbalance = f.get("imbalance_top5") or 0.0
    vwap = f.get("vwap")
    volume_ok = (f.get("volume_ratio") or 0) >= float(p["min_volume_ratio"])
    if position == 0:
        if (
            f["delta_5m"] > 0
            and ratio is not None
            and ratio >= float(p["min_ratio"])
            and imbalance >= float(p["min_imbalance"])
            and vwap
            and price > vwap
            and volume_ok
        ):
            score = min(1.0, 0.4 + 0.1 * ratio + imbalance)
            return Decision(
                1,
                ["POSITIVE_DELTA", "AGGRESSIVE_BUYING", "BID_IMBALANCE", "ABOVE_VWAP", "VOLUME_EXPANSION"],
                score,
                price - p["stop_ticks"] * tick,
                price + p["target_ticks"] * tick,
            )
        if (
            p["allow_short"]
            and f["delta_5m"] < 0
            and ratio is not None
            and ratio <= 1 / float(p["min_ratio"])
            and imbalance <= -float(p["min_imbalance"])
            and vwap
            and price < vwap
            and volume_ok
        ):
            score = min(1.0, 0.4 + 0.1 / max(ratio, 0.05) + abs(imbalance))
            return Decision(
                -1,
                ["NEGATIVE_DELTA", "AGGRESSIVE_SELLING", "ASK_IMBALANCE", "BELOW_VWAP", "VOLUME_EXPANSION"],
                score,
                price + p["stop_ticks"] * tick,
                price - p["target_ticks"] * tick,
            )
        return None
    if position > 0 and f["delta_1m"] < 0 and (ratio or 0) < 1:
        return Decision(0, ["ORDER_FLOW_REVERSAL"])
    if position < 0 and f["delta_1m"] > 0 and (ratio or 99) > 1:
        return Decision(0, ["ORDER_FLOW_REVERSAL"])
    return None


def liquidity_wall_decision(f: dict[str, Any], position: int, p: dict[str, Any]) -> Decision | None:
    """Buy just above a large, persistent bid wall that is absorbing selling; leave if the wall goes."""
    if not f or f.get("price") is None:
        return None
    price, tick = f["price"], f["tick"]
    if position == 0:
        for side, direction in (("bid_wall", 1), ("ask_wall", -1)):
            wall = f.get(side)
            if wall is None or (direction < 0 and not p["allow_short"]):
                continue
            big = (wall.get("size_vs_typical") or 0) >= float(p["min_size_vs_typical"])
            near = wall["distance_ticks"] <= p["max_distance_ticks"]
            lasting = wall["age_seconds"] >= p["min_wall_seconds"]
            confirmed = not p["require_confirmation"] or bool(wall.get("confirmed_by"))
            absorbing = f["bid_absorption"] if direction > 0 else f["ask_absorption"]
            flow_ok = (f["delta_1m"] >= 0) if direction > 0 else (f["delta_1m"] <= 0)
            if big and near and lasting and confirmed and (absorbing or flow_ok):
                reasons = ["BID_WALL_SUPPORT" if direction > 0 else "ASK_WALL_RESISTANCE"]
                reasons += ["ABSORPTION"] if absorbing else []
                reasons += ["CROSS_FEED_CONFIRMED"] if wall.get("confirmed_by") else []
                stop = wall["price"] - direction * p["stop_ticks"] * tick
                return Decision(
                    direction,
                    reasons,
                    0.6 + (0.2 if absorbing else 0) + (0.1 if wall.get("confirmed_by") else 0),
                    stop,
                    price + direction * p["target_ticks"] * tick,
                )
        return None
    if position > 0 and (f["bid_wall_withdrawn"] or f.get("bid_wall") is None):
        return Decision(0, ["WALL_WITHDRAWN"])
    if position < 0 and (f["ask_wall_withdrawn"] or f.get("ask_wall") is None):
        return Decision(0, ["WALL_WITHDRAWN"])
    return None


FLOW_PARAMS = {
    "quantity": Param(Decimal, "1", min=Decimal("0"), description="Position size"),
    "allow_short": Param(bool, False, description="Take short positions too"),
    "stop_ticks": Param(int, 10, min=1, max=10_000),
    "target_ticks": Param(int, 20, min=1, max=10_000),
    "trail_pct": Param(
        Decimal, "0", min=Decimal(0), max=Decimal(50), description="Trailing stop, 0 for none"
    ),
    "max_hold_minutes": Param(int, 30, min=1, max=100_000),
}


class _FeatureStrategy(Strategy):
    decide = staticmethod(order_flow_decision)

    def on_bar(self, candle: Candle) -> None:
        iid = candle.instrument_id
        if self.ctx.open_orders(iid):
            return
        position = self.ctx.position(iid)
        direction = 0 if position == 0 else (1 if position > 0 else -1)
        exit_reason = self.ctx.exits.check(
            iid, high=float(candle.high), low=float(candle.low), close=float(candle.close), at=candle.close_ts
        )
        if direction and exit_reason:
            self._exit(iid, exit_reason)
            return
        decision = self.decide(self.ctx.features(iid), direction, self.ctx.params)
        if decision is None:
            return
        if decision.target == 0 and direction:
            self._exit(iid, decision.reasons[0])
        elif decision.target != 0 and direction == 0:
            p = self.ctx.params
            self.ctx.explain(
                *decision.reasons, confidence=decision.confidence, stop=decision.stop, target=decision.take
            )
            self.ctx.order_target(iid, p["quantity"] * decision.target)
            self.ctx.exits.attach(
                iid,
                ExitPlan(
                    decision.target,
                    float(candle.close),
                    candle.close_ts,
                    stop=decision.stop,
                    target=decision.take,
                    trail_pct=float(p["trail_pct"]) or None,
                    time_limit_minutes=p["max_hold_minutes"],
                ),
            )

    def _exit(self, iid: str, reason: str) -> None:
        self.ctx.explain(reason, exit_reason=reason)
        self.ctx.order_target(iid, Decimal(0))
        self.ctx.exits.clear(iid)


class OrderFlowMomentum(_FeatureStrategy):
    name = "order_flow_momentum"
    description = (
        "Enter with the flow when aggressive buying (selling) dominates, the visible book leans the same way "
        "and price is above (below) VWAP on busy volume; exit on order-flow reversal, stop, target or time. "
        "Needs live order books; test it by replaying recorded sessions."
    )
    parameters = {
        **FLOW_PARAMS,
        "min_ratio": Param(Decimal, "2", min=Decimal("1.01"), max=Decimal(50)),
        "min_imbalance": Param(Decimal, "0.2", min=Decimal(0), max=Decimal(1)),
        "min_volume_ratio": Param(Decimal, "1.5", min=Decimal(0), max=Decimal(50)),
    }
    decide = staticmethod(order_flow_decision)


class LiquidityWallBounce(_FeatureStrategy):
    name = "liquidity_wall_bounce"
    description = (
        "Buy just above a large, lasting bid wall (sell under an ask wall) that is absorbing aggressive "
        "orders, with the stop beyond the wall; exit at once if the wall is withdrawn. "
        "Needs live order books."
    )
    parameters = {
        **FLOW_PARAMS,
        "min_size_vs_typical": Param(Decimal, "20", min=Decimal(2), max=Decimal(10_000)),
        "max_distance_ticks": Param(int, 5, min=0, max=1000),
        "min_wall_seconds": Param(int, 20, min=0, max=100_000),
        "require_confirmation": Param(
            bool, False, description="Wall must be visible on a second broker feed"
        ),
    }
    decide = staticmethod(liquidity_wall_decision)


class VwapReclaim(Strategy):
    name = "vwap_reclaim"
    description = (
        "Buy when price closes back above the session VWAP on above-average volume (sell short on a close "
        "back below, if allowed); exit on the opposite cross, the stop, target, trailing stop or time limit."
    )
    parameters = {
        "quantity": Param(Decimal, "1", min=Decimal("0")),
        "allow_short": Param(bool, False),
        "volume_multiple": Param(Decimal, "1.5", min=Decimal(0), max=Decimal(20)),
        "volume_lookback": Param(int, 20, min=2, max=500),
        "stop_pct": Param(Decimal, "0.5", min=Decimal(0), max=Decimal(50)),
        "target_pct": Param(Decimal, "1.0", min=Decimal(0), max=Decimal(100)),
        "trail_pct": Param(Decimal, "0", min=Decimal(0), max=Decimal(50)),
        "max_hold_minutes": Param(int, 390, min=1, max=100_000),
    }

    def on_bar(self, candle: Candle) -> None:
        iid = candle.instrument_id
        if self.ctx.open_orders(iid):
            return
        p = self.ctx.params
        bars = self.ctx.candles(iid, 800)
        session = _session_bars(bars)
        if len(session) < 3:
            return
        vwaps = _vwap_path(session)
        if vwaps is None:
            return
        prev_close, close = float(session[-2].close), float(session[-1].close)
        prev_vwap, vwap = vwaps[-2], vwaps[-1]
        volumes = [float(b.volume) for b in bars[-p["volume_lookback"] - 1 : -1]]
        busy = volumes and float(candle.volume) >= float(p["volume_multiple"]) * (sum(volumes) / len(volumes))
        position = self.ctx.position(iid)
        direction = 0 if position == 0 else (1 if position > 0 else -1)
        reason = self.ctx.exits.check(
            iid, high=float(candle.high), low=float(candle.low), close=close, at=candle.close_ts
        )
        crossed_up = prev_close <= prev_vwap and close > vwap
        crossed_down = prev_close >= prev_vwap and close < vwap
        if direction > 0 and (reason or crossed_down):
            self._exit(iid, reason or "VWAP_REJECTION")
        elif direction < 0 and (reason or crossed_up):
            self._exit(iid, reason or "VWAP_RECLAIM")
        elif direction == 0 and busy and (crossed_up or (crossed_down and p["allow_short"])):
            d = 1 if crossed_up else -1
            stop = close * (1 - d * float(p["stop_pct"]) / 100) if float(p["stop_pct"]) else None
            take = close * (1 + d * float(p["target_pct"]) / 100) if float(p["target_pct"]) else None
            self.ctx.explain(
                "VWAP_RECLAIM" if d > 0 else "VWAP_REJECTION",
                "VOLUME_EXPANSION",
                confidence=0.55,
                stop=stop,
                target=take,
            )
            self.ctx.order_target(iid, p["quantity"] * d)
            self.ctx.exits.attach(
                iid,
                ExitPlan(
                    d,
                    close,
                    candle.close_ts,
                    stop=stop,
                    target=take,
                    trail_pct=float(p["trail_pct"]) or None,
                    time_limit_minutes=p["max_hold_minutes"],
                ),
            )

    def _exit(self, iid: str, reason: str) -> None:
        self.ctx.explain(reason, exit_reason=reason)
        self.ctx.order_target(iid, Decimal(0))
        self.ctx.exits.clear(iid)


def _session_bars(bars: list[Candle]) -> list[Candle]:
    """Bars of the latest session: back to the last gap of more than four hours."""
    out = []
    for bar in reversed(bars):
        if out and (out[-1].open_ts - bar.open_ts).total_seconds() > 4 * 3600:
            break
        out.append(bar)
    return list(reversed(out))


def _vwap_path(bars: list[Candle]) -> list[float] | None:
    volume = turnover = 0.0
    out = []
    for b in bars:
        v = float(b.volume)
        volume += v
        turnover += v * (float(b.high) + float(b.low) + float(b.close)) / 3
        out.append(turnover / volume if volume else float(b.close))
    return out if volume > 0 else None
