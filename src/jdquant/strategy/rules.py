"""Rule-based strategies written as data: what the AI produces from a strategy typed in plain words.

A spec is JSON, never code, so a typed strategy can be validated, shown back in plain English, backtested
and traded without running anything the user or the AI wrote. Example:

    {"name": "EMA trend with RSI filter",
     "long_entry": {"all": [
         {"left": {"ind": "ema", "period": 20}, "op": "crosses_above", "right": {"ind": "ema", "period": 50}},
         {"left": {"ind": "rsi", "period": 14}, "op": ">", "right": {"value": 50}}]},
     "long_exit": {"left": {"ind": "close"}, "op": "<", "right": {"ind": "ema", "period": 50}},
     "stop_loss_pct": 2, "take_profit_pct": 4}

Conditions: {"all": [...]}, {"any": [...]}, {"not": cond}, or a comparison {"left", "op", "right"} with op
one of > < >= <= crosses_above crosses_below, or {"left", "op": "rising"|"falling", "bars": n}.
Operands: {"value": number} or {"ind": name, ...parameters, "offset": bars_ago, "mult": factor}.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from decimal import ROUND_FLOOR, Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.records import Candle
from jdquant.markets.sessions import session_for
from jdquant.strategy.base import Param, Strategy
from jdquant.strategy.indicators import (
    atr,
    bollinger,
    ema_series,
    macd,
    rate_of_change,
    rsi,
    sma,
    supertrend,
    zscore,
)

# indicator -> {parameter: (default, min, max)}
INDICATORS: dict[str, dict[str, tuple[Any, float, float]]] = {
    "close": {}, "open": {}, "high": {}, "low": {}, "volume": {},
    "sma": {"period": (20, 1, 500)},
    "ema": {"period": (20, 1, 500)},
    "rsi": {"period": (14, 2, 200)},
    "macd": {"fast": (12, 2, 200), "slow": (26, 3, 400), "signal": (9, 2, 100)},
    "bb": {"period": (20, 2, 400), "k": (2, 0.5, 5)},
    "atr": {"period": (14, 2, 200)},
    "highest": {"period": (20, 1, 500)},
    "lowest": {"period": (20, 1, 500)},
    "roc": {"period": (10, 1, 500)},
    "stoch": {"period": (14, 2, 200)},
    "zscore": {"period": (20, 2, 500)},
    "supertrend": {"period": (10, 2, 200), "k": (3, 0.5, 10)},
    "vwap": {},
}  # fmt: skip
CHOICES = {
    "macd": ("line", ("macd", "signal", "hist")),
    "bb": ("band", ("upper", "middle", "lower")),
    "highest": ("source", ("high", "close", "low")),
    "lowest": ("source", ("low", "close", "high")),
}
OPS = (">", "<", ">=", "<=", "crosses_above", "crosses_below", "rising", "falling")
SIDES = ("long_entry", "long_exit", "short_entry", "short_exit")
MAX_DEPTH = 6


# ---- validation --------------------------------------------------------------------------------


def validate_spec(spec: Any) -> dict[str, Any]:
    """A normalised copy of the spec, or ValidationError listing every problem."""
    problems: list[dict[str, str]] = []
    if not isinstance(spec, dict):
        raise ValidationError("RULES_INVALID", [{"field": "spec", "message": "must be a JSON object"}])
    out: dict[str, Any] = {
        "name": str(spec.get("name") or "Typed strategy")[:120],
        "description": str(spec.get("description") or "")[:600],
    }
    for side in SIDES:
        cond = spec.get(side)
        out[side] = None if cond in (None, {}) else _condition(cond, side, problems, 0)
    if out["long_entry"] is None and out["short_entry"] is None:
        problems.append({"field": "long_entry", "message": "a strategy needs an entry rule (long or short)"})
    for key, low, high in (
        ("stop_loss_pct", 0.01, 50),
        ("take_profit_pct", 0.01, 500),
        ("trailing_stop_pct", 0.01, 50),
        ("size_pct", 1, 100),
        ("exit_after_bars", 1, 10_000),
    ):
        value = spec.get(key)
        if value in (None, 0, ""):
            out[key] = None if key != "size_pct" else 100
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append({"field": key, "message": "must be a number"})
            continue
        if not low <= number <= high:
            problems.append({"field": key, "message": f"between {low} and {high}"})
        out[key] = int(number) if key == "exit_after_bars" else number
    session = spec.get("session")
    out["session"] = None
    if session:
        try:
            tz = str(session.get("tz") or "UTC")
            ZoneInfo(tz)
            out["session"] = {"tz": tz, "start": _hhmm(session["start"]), "end": _hhmm(session["end"])}
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError, AttributeError):
            problems.append(
                {
                    "field": "session",
                    "message": 'use {"tz": "Europe/London", "start": "07:00", "end": "11:00"}',
                }
            )
    out["flat_at_cutoff"] = bool(spec.get("flat_at_cutoff", False))
    if problems:
        raise ValidationError("RULES_INVALID", problems)
    return out


def _hhmm(value: str) -> str:
    return time.fromisoformat(str(value)).strftime("%H:%M")


def _condition(cond: Any, path: str, problems: list, depth: int) -> dict | None:
    if depth > MAX_DEPTH or not isinstance(cond, dict):
        problems.append({"field": path, "message": "each condition must be an object"})
        return None
    for key in ("all", "any"):
        if key in cond:
            items = cond[key]
            if not isinstance(items, list) or not items:
                problems.append({"field": path, "message": f"'{key}' needs a list of conditions"})
                return None
            return {
                key: [_condition(c, f"{path}.{key}[{i}]", problems, depth + 1) for i, c in enumerate(items)]
            }
    if "not" in cond:
        return {"not": _condition(cond["not"], f"{path}.not", problems, depth + 1)}
    op = cond.get("op")
    if op not in OPS:
        problems.append({"field": path, "message": f"op must be one of {', '.join(OPS)}"})
        return None
    left = _operand(cond.get("left"), f"{path}.left", problems)
    if op in ("rising", "falling"):
        bars = cond.get("bars", 1)
        if not isinstance(bars, int) or not 1 <= bars <= 200:
            problems.append({"field": f"{path}.bars", "message": "a whole number of bars from 1 to 200"})
        return {"left": left, "op": op, "bars": bars}
    return {"left": left, "op": op, "right": _operand(cond.get("right"), f"{path}.right", problems)}


def _operand(operand: Any, path: str, problems: list) -> dict | None:
    if not isinstance(operand, dict):
        problems.append({"field": path, "message": 'must be {"value": n} or {"ind": name, ...}'})
        return None
    if "value" in operand:
        try:
            return {"value": float(operand["value"])}
        except (TypeError, ValueError):
            problems.append({"field": path, "message": "value must be a number"})
            return None
    name = operand.get("ind")
    if name not in INDICATORS:
        problems.append(
            {"field": path, "message": f"unknown indicator {name!r}; use one of {', '.join(INDICATORS)}"}
        )
        return None
    out: dict[str, Any] = {"ind": name}
    for param, (default, low, high) in INDICATORS[name].items():
        raw = operand.get(param, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            problems.append({"field": f"{path}.{param}", "message": "must be a number"})
            continue
        if not low <= value <= high:
            problems.append({"field": f"{path}.{param}", "message": f"between {low} and {high}"})
        out[param] = int(value) if isinstance(default, int) else value
    if name in CHOICES:
        key, allowed = CHOICES[name]
        choice = operand.get(key, allowed[0])
        if choice not in allowed:
            problems.append({"field": f"{path}.{key}", "message": f"one of {', '.join(allowed)}"})
        out[key] = choice
    if name == "macd" and out.get("fast", 0) >= out.get("slow", 1):
        problems.append({"field": path, "message": "MACD fast period must be shorter than slow"})
    offset = operand.get("offset", 0)
    if not isinstance(offset, int) or not 0 <= offset <= 200:
        problems.append({"field": f"{path}.offset", "message": "bars ago, 0 to 200"})
    out["offset"] = offset if isinstance(offset, int) else 0
    try:
        out["mult"] = float(operand.get("mult", 1))
    except (TypeError, ValueError):
        problems.append({"field": f"{path}.mult", "message": "must be a number"})
    return out


def parse_spec(text: str) -> dict[str, Any]:
    try:
        return validate_spec(json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValidationError("RULES_INVALID", [{"field": "spec", "message": f"not JSON: {exc}"}]) from None


# ---- plain English ------------------------------------------------------------------------------

NAMES = {
    "close": "close", "open": "open", "high": "high", "low": "low", "volume": "volume",
    "sma": "SMA({period})", "ema": "EMA({period})", "rsi": "RSI({period})", "atr": "ATR({period})",
    "macd": "MACD({fast},{slow},{signal}) {line}", "bb": "Bollinger({period},{k}) {band}",
    "highest": "highest {source} of {period} bars", "lowest": "lowest {source} of {period} bars",
    "roc": "{period}-bar change", "stoch": "Stochastic %K({period})", "zscore": "z-score({period})",
    "supertrend": "Supertrend({period},{k}) direction (1 up, -1 down)", "vwap": "session VWAP",
}  # fmt: skip
OP_WORDS = {
    ">": "is above", "<": "is below", ">=": "is at or above", "<=": "is at or below",
    "crosses_above": "crosses above", "crosses_below": "crosses below",
}  # fmt: skip


def describe_operand(o: dict) -> str:
    if "value" in o:
        return f"{o['value']:g}"
    text = NAMES[o["ind"]].format(**{k: (f"{v:g}" if isinstance(v, float) else v) for k, v in o.items()})
    if o.get("mult", 1) != 1:
        text = f"{o['mult']:g} × {text}"
    if o.get("offset"):
        text += f" {o['offset']} bars ago"
    return text


def describe_condition(c: dict) -> str:
    if "all" in c:
        return " and ".join(
            f"({describe_condition(x)})" if "any" in x else describe_condition(x) for x in c["all"]
        )
    if "any" in c:
        return " or ".join(describe_condition(x) for x in c["any"])
    if "not" in c:
        return f"not ({describe_condition(c['not'])})"
    if c["op"] in ("rising", "falling"):
        return f"{describe_operand(c['left'])} is {c['op']} over {c['bars']} bars"
    return f"{describe_operand(c['left'])} {OP_WORDS[c['op']]} {describe_operand(c['right'])}"


def describe(spec: dict) -> list[str]:
    lines = []
    words = {"long_entry": "Buy when", "long_exit": "Sell the long when", "short_entry": "Sell short when",
             "short_exit": "Buy back the short when"}  # fmt: skip
    for side in SIDES:
        if spec.get(side):
            lines.append(f"{words[side]} {describe_condition(spec[side])}.")
    extras = []
    if spec.get("stop_loss_pct"):
        extras.append(f"stop-loss {spec['stop_loss_pct']:g}%")
    if spec.get("take_profit_pct"):
        extras.append(f"take-profit {spec['take_profit_pct']:g}%")
    if spec.get("trailing_stop_pct"):
        extras.append(f"trailing stop {spec['trailing_stop_pct']:g}%")
    if spec.get("exit_after_bars"):
        extras.append(f"exit after {spec['exit_after_bars']} bars")
    if spec.get("flat_at_cutoff"):
        extras.append("flat before the session's intraday cutoff")
    if extras:
        lines.append("Risk: " + ", ".join(extras) + ".")
    if spec.get("session"):
        s = spec["session"]
        lines.append(f"New trades only between {s['start']} and {s['end']} ({s['tz']}).")
    lines.append(
        f"Each trade uses {spec.get('size_pct') or 100:g}% of the capital (times any leverage allowed)."
    )
    return lines


# ---- evaluation ---------------------------------------------------------------------------------


def lookback(o: dict) -> int:
    if "value" in o:
        return 1
    p = o.get("period", 1)
    need = {
        "ema": p * 4 + 10, "rsi": p * 6, "atr": p * 6, "supertrend": p * 10,
        "macd": (o.get("slow", 26) + o.get("signal", 9)) * 4, "vwap": 1500,
    }.get(o["ind"], p + 2)  # fmt: skip
    return need + o.get("offset", 0) + 2


def value(o: dict, candles: list[Candle], shift: int = 0, tz=None) -> float | None:
    """The operand's value `shift` bars before the latest candle (plus its own offset)."""
    if "value" in o:
        return o["value"]
    cut = len(candles) - shift - o.get("offset", 0)
    if cut <= 0:
        return None
    window = candles[max(0, cut - lookback(o)) : cut]
    result = _indicator(o, window, tz)
    return None if result is None else result * o.get("mult", 1)


def _indicator(o: dict, w: list[Candle], tz) -> float | None:
    name = o["ind"]
    if name in ("close", "open", "high", "low", "volume"):
        return float(getattr(w[-1], name))
    closes = [c.close for c in w]
    match name:
        case "sma":
            return sma(closes, o["period"])
        case "ema":
            series = ema_series(closes, o["period"])
            return series[-1] if series else None
        case "rsi":
            return rsi(closes, o["period"])
        case "macd":
            values = macd(closes, o["fast"], o["slow"], o["signal"])
            if values is None:
                return None
            line, signal = values
            return {"macd": line, "signal": signal, "hist": line - signal}[o["line"]]
        case "bb":
            bands = bollinger(closes, o["period"], o["k"])
            return (
                None
                if bands is None
                else dict(zip(("lower", "middle", "upper"), bands, strict=True))[o["band"]]
            )
        case "atr":
            return atr([c.high for c in w], [c.low for c in w], closes, o["period"])
        case "highest" | "lowest":
            if len(w) < o["period"]:
                return None
            values = [float(getattr(c, o["source"])) for c in w[-o["period"] :]]
            return max(values) if name == "highest" else min(values)
        case "roc":
            change = rate_of_change(closes, o["period"])
            return None if change is None else change * 100
        case "stoch":
            if len(w) < o["period"]:
                return None
            recent = w[-o["period"] :]
            high, low = max(float(c.high) for c in recent), min(float(c.low) for c in recent)
            return 50.0 if high == low else (float(w[-1].close) - low) / (high - low) * 100
        case "zscore":
            return zscore(closes, o["period"])
        case "supertrend":
            result = supertrend([c.high for c in w], [c.low for c in w], closes, o["period"], o["k"])
            return None if result is None else (1.0 if result[0] else -1.0)
        case "vwap":
            day = w[-1].open_ts.astimezone(tz).date() if tz else w[-1].open_ts.date()
            today = [c for c in w if (c.open_ts.astimezone(tz).date() if tz else c.open_ts.date()) == day]
            volume = sum(float(c.volume) for c in today)
            if not volume:
                return sum(float(c.close) for c in today) / len(today)
            return sum(float((c.high + c.low + c.close) / 3) * float(c.volume) for c in today) / volume
    return None


def holds(c: dict | None, candles: list[Candle], tz=None) -> bool:
    if not c:
        return False
    if "all" in c:
        return all(holds(x, candles, tz) for x in c["all"])
    if "any" in c:
        return any(holds(x, candles, tz) for x in c["any"])
    if "not" in c:
        return not holds(c["not"], candles, tz)
    op = c["op"]
    if op in ("rising", "falling"):
        now, before = value(c["left"], candles, 0, tz), value(c["left"], candles, c["bars"], tz)
        if now is None or before is None:
            return False
        return now > before if op == "rising" else now < before
    left, right = value(c["left"], candles, 0, tz), value(c["right"], candles, 0, tz)
    if left is None or right is None:
        return False
    match op:
        case ">":
            return left > right
        case "<":
            return left < right
        case ">=":
            return left >= right
        case "<=":
            return left <= right
    prev_left, prev_right = value(c["left"], candles, 1, tz), value(c["right"], candles, 1, tz)
    if prev_left is None or prev_right is None:
        return False
    if op == "crosses_above":
        return prev_left <= prev_right and left > right
    return prev_left >= prev_right and left < right


def spec_lookback(spec: dict) -> int:
    need = 50

    def walk(c):
        nonlocal need
        if not c:
            return
        for key in ("all", "any"):
            for x in c.get(key, []):
                walk(x)
        if "not" in c:
            walk(c["not"])
        for side in ("left", "right"):
            if isinstance(c.get(side), dict):
                need = max(need, lookback(c[side]) + c.get("bars", 1) + 1)

    for side in SIDES:
        walk(spec.get(side))
    return min(need, 3000)


# ---- the strategy -------------------------------------------------------------------------------


class RuleStrategy(Strategy):
    """Trades a validated rule spec: entries and exits on bar closes, with stops and session limits."""

    name = "rules"
    version = "1.0.0"
    description = "A strategy written as rules (typed in plain words and translated by the AI)."
    parameters = {
        "spec": Param(str, "{}", description="the rules as JSON (see the strategy lab)"),
        "capital": Param(Decimal, "100000", min=Decimal(0), description="capital, quote currency"),
        "leverage": Param(Decimal, "1", min=Decimal(1), max=Decimal(50), description="1 = no leverage"),
        "allow_short": Param(bool, True, description="short entries are allowed where the market permits"),
        "trade_after": Param(str, "", description="ISO time; bars closing earlier only warm up"),
    }

    def on_init(self) -> None:
        p = self.ctx.params
        try:
            self.spec = parse_spec(p["spec"])
        except ValidationError as exc:
            raise PlatformError("PARAMETER_INVALID", f"invalid rules: {exc.details}") from None
        self._need = spec_lookback(self.spec)
        self._trade_after = datetime.fromisoformat(p["trade_after"]) if p["trade_after"] else None
        self._entry_bar: dict[str, int] = {}
        self._best: dict[str, Decimal] = {}
        self._bars = 0

    def on_bar(self, candle: Candle) -> None:
        self._bars += 1
        i = candle.instrument_id
        if self._trade_after is not None and candle.close_ts < self._trade_after:
            return
        if self.ctx.open_orders(i):
            return
        instrument = self.ctx.instrument(i)
        session = session_for(instrument)
        candles = self.ctx.candles(i, self._need)
        position = self.ctx.position(i)
        spec = self.spec
        late = spec["flat_at_cutoff"] and session.past_intraday_cutoff(candle.close_ts)
        if position:
            direction = 1 if position > 0 else -1
            exit_rule = spec["long_exit"] if direction > 0 else spec["short_exit"]
            opposite = spec["short_entry"] if direction > 0 else spec["long_entry"]
            held = self._bars - self._entry_bar.get(i, self._bars)
            reverse = holds(opposite, candles, session.tz)
            if (
                late
                or reverse
                or self._stopped(i, candle.close, direction)
                or holds(exit_rule, candles, session.tz)
                or (spec["exit_after_bars"] and held >= spec["exit_after_bars"])
            ):
                # The opposite entry firing on this bar reverses the position in one order.
                can_reverse = (
                    reverse
                    and not late
                    and self._in_window(candle.close_ts)
                    and (direction < 0 or (self.ctx.params["allow_short"] and instrument.can_short))
                )
                extra = self._size(i, candle.close) if can_reverse else Decimal(0)
                self._best.pop(i, None)
                if direction > 0:
                    self.ctx.sell(i, position + extra)
                else:
                    self.ctx.buy(i, -position + extra)
                if extra:
                    self._entry_bar[i] = self._bars
            return
        self._best.pop(i, None)
        if late or not self._in_window(candle.close_ts):
            return
        if holds(spec["long_entry"], candles, session.tz):
            quantity = self._size(i, candle.close)
            if quantity > 0 and self.ctx.buy(i, quantity) is not None:
                self._entry_bar[i] = self._bars
        elif (
            self.ctx.params["allow_short"]
            and instrument.can_short
            and holds(spec["short_entry"], candles, session.tz)
        ):
            quantity = self._size(i, candle.close)
            if quantity > 0 and self.ctx.sell(i, quantity) is not None:
                self._entry_bar[i] = self._bars

    def _in_window(self, at: datetime) -> bool:
        window = self.spec["session"]
        if not window:
            return True
        local = at.astimezone(ZoneInfo(window["tz"])).time()
        start, end = time.fromisoformat(window["start"]), time.fromisoformat(window["end"])
        return start <= local < end if start <= end else local >= start or local < end

    def _stopped(self, instrument_id: str, price: Decimal, direction: int) -> bool:
        spec = self.spec
        entry = self.ctx.entry_price(instrument_id)
        if entry <= 0:
            return False
        move = float((price - entry) / entry) * direction * 100
        if spec["stop_loss_pct"] and move <= -spec["stop_loss_pct"]:
            return True
        if spec["take_profit_pct"] and move >= spec["take_profit_pct"]:
            return True
        best = self._best.get(instrument_id, entry)
        best = max(best, price) if direction > 0 else min(best, price)
        self._best[instrument_id] = best
        giveback = float((best - price) / best) * direction * 100
        return bool(spec["trailing_stop_pct"] and giveback >= spec["trailing_stop_pct"])

    def _size(self, instrument_id: str, price: Decimal) -> Decimal:
        p = self.ctx.params
        instrument = self.ctx.instrument(instrument_id)
        notional = p["capital"] * p["leverage"] * Decimal(str(self.spec["size_pct"])) / 100
        lots = (notional / (price * instrument.contract_multiplier) / instrument.lot_size).to_integral_value(
            rounding=ROUND_FLOOR
        )
        quantity = lots * instrument.lot_size
        return quantity if quantity >= instrument.min_quantity else Decimal(0)


# ---- the rules as code ------------------------------------------------------------------------------

CODE_HEADER = '''"""{name}

Generated by JD Quant from the validated rules, so you can read and review the strategy as code.
The platform runs these same rules through its rule engine; it never executes code written by an AI.

Every indicator call returns its value on the latest closed bar, or `ago` bars earlier. A value that
needs more history than exists is missing, and a condition that uses it is false.
"""
'''


def _code_operand(o: dict, shift: int = 0) -> str:
    if "value" in o:
        return f"{o['value']:g}"
    args = ["bars"]
    for key, value_ in o.items():
        if key in ("ind", "offset", "mult"):
            continue
        args.append(f"{key}={value_!r}" if isinstance(value_, str) else f"{key}={value_:g}")
    ago = o.get("offset", 0) + shift
    if ago:
        args.append(f"ago={ago}")
    text = f"{o['ind']}({', '.join(args)})"
    return f"{o['mult']:g} * {text}" if o.get("mult", 1) != 1 else text


def _code_condition(c: dict) -> str:
    if "all" in c:
        return " and ".join(f"({_code_condition(x)})" if "any" in x else _code_condition(x) for x in c["all"])
    if "any" in c:
        return " or ".join(f"({_code_condition(x)})" if "all" in x else _code_condition(x) for x in c["any"])
    if "not" in c:
        return f"not ({_code_condition(c['not'])})"
    op, left = c["op"], c["left"]
    if op in ("rising", "falling"):
        sign = ">" if op == "rising" else "<"
        return f"{_code_operand(left)} {sign} {_code_operand(left, c['bars'])}"
    right = c["right"]
    if op in ("crosses_above", "crosses_below"):
        before, after = ("<=", ">") if op == "crosses_above" else (">=", "<")
        return (
            f"({_code_operand(left, 1)} {before} {_code_operand(right, 1)} "
            f"and {_code_operand(left)} {after} {_code_operand(right)})"
        )
    return f"{_code_operand(left)} {op} {_code_operand(right)}"


def rules_to_python(spec: dict) -> str:
    """The validated rules as readable Python, generated deterministically (no AI involved)."""
    spec = validate_spec(spec)
    lines = [CODE_HEADER.format(name=spec["name"]).rstrip(), ""]
    settings = {
        "STOP_LOSS_PCT": spec.get("stop_loss_pct"),
        "TAKE_PROFIT_PCT": spec.get("take_profit_pct"),
        "TRAILING_STOP_PCT": spec.get("trailing_stop_pct"),
        "EXIT_AFTER_BARS": spec.get("exit_after_bars"),
        "SIZE_PCT_OF_CAPITAL": spec.get("size_pct") or 100,
        "FLAT_AT_INTRADAY_CUTOFF": spec.get("flat_at_cutoff", False),
        "SESSION": spec.get("session"),
    }
    lines += [f"{k} = {v!r}" for k, v in settings.items()]
    titles = {
        "long_entry": "Open a long position when this is true.",
        "long_exit": "Close the long position when this is true (stops and targets apply as well).",
        "short_entry": "Open a short position when this is true (where the market allows shorting).",
        "short_exit": "Close the short position when this is true (stops and targets apply as well).",
    }
    for side in SIDES:
        lines += ["", "", f"def {side}(bars) -> bool:", f'    """{titles[side]}"""']
        cond = spec.get(side)
        if cond and ("all" in cond or "any" in cond) and len(_code_condition(cond)) > 80:
            key = "all" if "all" in cond else "any"
            joiner = "and" if key == "all" else "or"
            parts = [
                f"({_code_condition(x)})" if ("any" in x or "all" in x) else _code_condition(x)
                for x in cond[key]
            ]
            lines += [
                "    return (",
                f"        {parts[0]}",
                *[f"        {joiner} {p}" for p in parts[1:]],
                "    )",
            ]
        elif cond:
            lines.append(f"    return {_code_condition(cond)}")
        else:
            lines.append("    return False  # no rule")
    return "\n".join(lines) + "\n"
