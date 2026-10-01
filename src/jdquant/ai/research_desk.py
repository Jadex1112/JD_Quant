"""The AI research desk: a TradingAgents-style debate that ends in an advisory rating.

After TradingAgents (Tauric Research, Apache-2.0: https://github.com/TauricResearch/TradingAgents), the
roles of a trading firm are played by language-model calls, all through the platform's configured model
(NVIDIA Nemotron, Kimi, Claude...):

    analysts (technical, market structure and flow, news and events, forecast)
      -> bull and bear researchers debate -> research manager -> trader proposal
      -> risk team (aggressive, neutral, conservative) -> portfolio manager's rating

It is written for this platform rather than imported: the analysts read the platform's own data (broker
history, the intelligence engines, the news desk, Kronos forecasts), so nothing depends on Yahoo Finance or
LangChain, and every figure a member may quote is in the data it was given.

The rating is advice for a person; nothing is traded from it. Language-model trading research has little
independent evidence of an edge (published results are short backtests over periods the models may have
read about), so every report is scored forward: after its horizon, the rating's direction is compared with
what the price did.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from jdquant.ai.prompts import (
    DESK_ANALYST,
    DESK_MANAGER,
    DESK_PORTFOLIO_MANAGER,
    DESK_RESEARCHER,
    DESK_RISK,
    DESK_TRADER,
)
from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.marketdata.records import Candle
from jdquant.strategy.indicators import atr, rsi, sma

KIND = "research_report"
RATINGS = {"BUY": 1, "OVERWEIGHT": 1, "HOLD": 0, "UNDERWEIGHT": -1, "SELL": -1}
DISCLAIMER = (
    "Advice from language models for a person to weigh, not an order. Scored forward below: judge it by "
    "that record, which starts empty."
)


def _json(text: str | None) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def technical_facts(candles: list[Candle]) -> dict[str, Any]:
    """Price statistics a technical analyst can quote (computed here, never by the model)."""
    closes = [float(c.close) for c in candles]
    highs, lows = [float(c.high) for c in candles], [float(c.low) for c in candles]
    last = closes[-1]

    def ret(n: int) -> float | None:
        return (last / closes[-1 - n] - 1) * 100 if len(closes) > n else None

    atr14 = atr(highs, lows, closes, 14)
    volumes = [float(c.volume) for c in candles]
    avg_volume = statistics.fmean(volumes[-21:-1]) if len(volumes) > 21 else None
    window = closes[-252:]
    return {
        "last_close": last,
        "bar": f"{int((candles[-1].close_ts - candles[-1].open_ts).total_seconds())}s",
        "last_bar_close": candles[-1].close_ts.isoformat(),
        "return_pct": {"1": ret(1), "5": ret(5), "20": ret(20), "60": ret(60)},
        "rsi14": rsi(closes, 14),
        "sma": {str(n): sma(closes, n) for n in (20, 50, 200)},
        "atr14": atr14,
        "atr14_pct": atr14 / last * 100 if atr14 else None,
        "range_high": max(window),
        "range_low": min(window),
        "range_bars": len(window),
        "volume_vs_20_bar_avg": volumes[-1] / avg_volume if avg_volume else None,
        "recent_closes": closes[-20:],
    }


class ResearchDesk:
    def __init__(
        self,
        platform,
        store,
        *,
        chat: Callable[[], Any],
        history: Callable[[Any, int, int], tuple[list[Candle], str]],
        intelligence: Any = None,
        forecasts: Any = None,
    ):
        self._p = platform
        self._store = store
        self._chat = chat  # returns the current ChatModel (or None)
        self._history = history
        self._intel = intelligence
        self._forecasts = forecasts
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ---- running the desk -----------------------------------------------------------------------------

    def start(
        self,
        instrument_id: str,
        *,
        user: str,
        interval_seconds: int = 86400,
        horizon: int = 5,
        debate_rounds: int = 1,
        model: str | None = None,
        use_forecast: bool = True,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Queue a desk run; it takes several model calls, so it runs in the background."""
        chat = self._chat()
        if chat is None:
            raise PlatformError(
                "AI_UNAVAILABLE", "set NVIDIA_API_KEY (or an Anthropic key) to run the research desk"
            )
        if model:
            if not hasattr(chat, "with_model"):
                raise PlatformError(
                    "MODEL_NOT_SWITCHABLE", f"{chat.provider} is configured; set NVIDIA_API_KEY to choose"
                )
            chat = chat.with_model(model)
        self._p.instruments.get(instrument_id)
        report_id = "R-" + uuid.uuid4().hex[:12]
        report = {
            "report_id": report_id,
            "instrument_id": instrument_id,
            "requested_by": user,
            "requested_at": self._p.clock.now().isoformat(),
            "interval_seconds": interval_seconds,
            "horizon": horizon,
            "debate_rounds": max(1, min(3, debate_rounds)),
            "model": f"{chat.provider} · {chat.model}",
            "status": "RUNNING",
            "steps": [],
            "error": None,
            "disclaimer": DISCLAIMER,
        }
        with self._lock:
            self._jobs[report_id] = report
        runner = threading.Thread(
            target=self._run, args=(report, chat, use_forecast), name=f"desk-{report_id}", daemon=True
        )
        runner.start()
        if wait:
            runner.join()
        return report

    def _step(self, report: dict, role: str, template, payload: dict, chat) -> dict:
        answer = _json(chat.ask(template, payload, max_tokens=6000, timeout=300))
        if answer is None:
            raise PlatformError("AI_NO_ANSWER", f"{role}: the model returned no usable JSON")
        report["steps"].append({"role": role, "at": self._p.clock.now().isoformat(), "output": answer})
        return answer

    def _run(self, report: dict, chat, use_forecast: bool) -> None:
        try:
            self._debate(report, chat, use_forecast)
            report["status"] = "DONE"
        except Exception as exc:  # the report shows what failed; nothing else depends on it
            report["status"] = "FAILED"
            report["error"] = str(exc)[:500]
        finally:
            report["finished_at"] = self._p.clock.now().isoformat()
            self._store.put(KIND, report["report_id"], report)
            with self._lock:
                self._jobs.pop(report["report_id"], None)

    def _debate(self, report: dict, chat, use_forecast: bool) -> None:
        iid = report["instrument_id"]
        instrument = self._p.instruments.get(iid)
        candles, source = self._history(instrument, report["interval_seconds"], 260)
        if len(candles) < 30:
            raise PlatformError("HISTORY_UNAVAILABLE", f"only {len(candles)} bars of history")
        facts = technical_facts(candles)
        report["history_source"] = source
        report["last_close"] = facts["last_close"]
        report["last_bar_close"] = facts["last_bar_close"]
        base = {
            "instrument": {
                "id": iid,
                "base": instrument.base_asset,
                "quote": instrument.quote_asset,
                "can_short": instrument.can_short,
            },
            "history_source": source,
        }

        data: dict[str, dict] = {"technical": facts}
        if self._intel is not None:
            try:
                analysis = self._intel.analyze(iid)
                data["market_structure"] = {
                    "regime": analysis.get("regime"),
                    "timeframes": analysis.get("timeframes"),
                    "alignment": analysis.get("alignment"),
                    "vwap": {k: v for k, v in (analysis.get("vwap") or {}).items() if k != "series"},
                    "levels": analysis.get("levels"),
                    "order_flow_and_walls": self._intel.features(iid),
                }
            except Exception:
                data["market_structure"] = {"missing": "no intraday data for this instrument"}
            news = self._intel.news.list(iid, 20)
            data["news"] = {
                "headlines": [
                    {"at": n["at"], "headline": n["headline"], "source": n["source"]} for n in news
                ],
                "corporate_events": self._intel.news.corporate(days=30, instrument_id=iid),
            }
        if use_forecast and self._forecasts is not None and self._forecasts.status()["available"]:
            try:
                f = self._forecasts.forecast(
                    iid, interval_seconds=report["interval_seconds"], horizon=report["horizon"]
                )
                data["forecast"] = {
                    "model": f["model"],
                    "prob_up": f["prob_up"],
                    "median_return_pct": f["median_return_pct"],
                    "p10": f["p10"],
                    "p90": f["p90"],
                    "horizon_bars": f["horizon"],
                    "track_record": self._forecasts.scorecard(iid),
                    "caveat": f["caveat"],
                }
            except PlatformError as exc:
                data["forecast"] = {"missing": exc.message}
        report["data"] = data

        analysts = {
            role: self._step(report, f"{role} analyst", DESK_ANALYST, {**base, "role": role, "data": d}, chat)
            for role, d in data.items()
        }
        debate: list[dict[str, Any]] = []
        for _ in range(report["debate_rounds"]):
            for side in ("bull", "bear"):
                turn = self._step(
                    report,
                    f"{side} researcher",
                    DESK_RESEARCHER,
                    {**base, "side": side, "analyst_reports": analysts, "debate_so_far": list(debate)},
                    chat,
                )
                debate.append({"side": side, **turn})
        plan = self._step(
            report,
            "research manager",
            DESK_MANAGER,
            {**base, "analyst_reports": analysts, "debate": debate},
            chat,
        )
        trade = self._step(
            report,
            "trader",
            DESK_TRADER,
            {
                **base,
                "plan": plan,
                "last_close": facts["last_close"],
                "atr14": facts["atr14"],
                "levels": (data.get("market_structure") or {}).get("levels"),
            },
            chat,
        )
        risk = self._step(
            report,
            "risk team",
            DESK_RISK,
            {**base, "proposal": trade, "technical": facts, "news": data.get("news")},
            chat,
        )
        final = self._step(
            report,
            "portfolio manager",
            DESK_PORTFOLIO_MANAGER,
            {**base, "plan": plan, "proposal": trade, "risk": risk},
            chat,
        )
        rating = str(final.get("rating", "HOLD")).upper()
        report["rating"] = rating if rating in RATINGS else "HOLD"
        report["confidence"] = final.get("confidence")
        report["summary"] = final.get("summary")
        report["trade"] = trade
        step = timedelta(seconds=report["interval_seconds"])
        report["target_time"] = (candles[-1].close_ts + step * report["horizon"]).isoformat()
        report["outcome"] = None

    # ---- reading and scoring ----------------------------------------------------------------------------

    def get(self, report_id: str) -> dict[str, Any]:
        with self._lock:
            running = self._jobs.get(report_id)
        if running is not None:
            return running
        doc = self._store.get(KIND, report_id)
        if doc is None:
            raise NotFoundError("REPORT_NOT_FOUND", f"unknown research report {report_id}")
        return doc

    def list(self, instrument_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            running = list(self._jobs.values())
        stored = [
            d for d in self._store.all(KIND) if instrument_id is None or d["instrument_id"] == instrument_id
        ]
        items = [r for r in running if instrument_id is None or r["instrument_id"] == instrument_id] + stored
        return sorted(items, key=lambda d: d["requested_at"], reverse=True)[:limit]

    def score_due(self) -> int:
        now = self._p.clock.now()
        scored = 0
        for doc in self._store.all(KIND):
            if doc.get("status") != "DONE" or doc.get("outcome") is not None or not doc.get("target_time"):
                continue
            if "synthetic" in (doc.get("history_source") or ""):
                continue  # demo data says nothing about the desk's judgement
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
                continue
            move = (float(actual.close) / doc["last_close"] - 1) * 100
            direction = RATINGS.get(doc["rating"], 0)
            doc["outcome"] = {
                "actual_close": float(actual.close),
                "return_pct": move,
                "hit": None if direction == 0 else (move > 0) == (direction > 0),
            }
            self._store.put(KIND, doc["report_id"], doc)
            scored += 1
        return scored

    def scorecard(self) -> dict[str, Any]:
        done = [d for d in self._store.all(KIND) if d.get("outcome")]
        calls = [d for d in done if d["outcome"]["hit"] is not None]
        if not calls:
            return {
                "scored": len(done),
                "directional": 0,
                "verdict": "No rating has reached its horizon yet.",
            }
        hit_rate = sum(d["outcome"]["hit"] for d in calls) / len(calls)
        signed = [d["outcome"]["return_pct"] * RATINGS[d["rating"]] for d in calls]
        return {
            "scored": len(done),
            "directional": len(calls),
            "hit_rate": hit_rate,
            "average_signed_return_pct": statistics.fmean(signed),
            "verdict": (
                f"{len(calls)} directional ratings scored: too few to judge (30 or more needed)."
                if len(calls) < 30
                else "Better than a coin flip so far."
                if hit_rate > 0.55 and statistics.fmean(signed) > 0
                else "Not better than a coin flip so far."
            ),
        }
