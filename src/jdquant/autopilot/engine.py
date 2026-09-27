"""The autopilot: research, deploy, monitor, retire and promote without a human in the loop.

Each cycle it loads history for the configured universe, runs walk-forward research, and acts on the result:
new winners start trading on the autopilot's own paper account, deployments whose edge no longer holds up
are closed out, and paper deployments with a clean track record move to the live broker account — but only
while a person has armed live trading with a capital cap. Every action is recorded as a decision with its
reasons. Risk limits and kill switches apply to everything it does, exactly as for manual trading.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.ai.training import TrainingConfig, train
from jdquant.autopilot.research import (
    Candidate,
    Evaluation,
    ResearchConfig,
    ResearchResult,
    research,
    strategy_parameters,
)
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.records import Candle
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.markets.india import IST, NseCalendar, Product
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.platform import Platform
from jdquant.security.audit import AuditLog
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount

log = logging.getLogger(__name__)

AUTOPILOT = "autopilot"
PAPER_AI_ACCOUNT = "paper-ai"
KIND = "autopilot"
DECISIONS = "autopilot_decision"
RUNS = "autopilot_run"
ACTIVE = (DeploymentState.RUNNING, DeploymentState.PAUSED, DeploymentState.READY)


@dataclass
class AutopilotConfig:
    enabled: bool = False  # run research cycles on a schedule
    universe: list[str] = field(default_factory=list)
    data_source: str = "auto"  # auto (broker history when connected, else synthetic) | venue | synthetic
    interval_seconds: int = 86400
    history_bars: int = 750
    capital: Decimal = Decimal(100_000)  # paper budget the autopilot allocates
    max_positions: int = 5
    max_weight: float = 0.4
    product: Product = Product.CNC
    stop_loss: Decimal = Decimal("0.08")
    take_profit: Decimal = Decimal(0)
    slippage_bps: Decimal = Decimal(5)
    min_sharpe: float = 0.5
    max_drawdown: float = 0.25
    min_dsr: float = 0.75
    min_trades: int = 5
    cycle_hours: float = 24.0  # for intraday bars; daily bars research after each close
    max_deployment_drawdown: float = 0.15  # of allocated capital, then the deployment is closed out
    min_paper_days: int = 10
    min_paper_trades: int = 2
    explain_with_claude: bool = True

    def research_config(self) -> ResearchConfig:
        return ResearchConfig(
            interval_seconds=self.interval_seconds,
            capital=self.capital,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            intraday=self.product is Product.INTRADAY,
            product=self.product,
            slippage_bps=self.slippage_bps,
            min_trades=self.min_trades,
            min_sharpe=self.min_sharpe,
            max_drawdown=self.max_drawdown,
            min_dsr=self.min_dsr,
            max_positions=self.max_positions,
            max_weight=self.max_weight,
        )


@dataclass
class LiveArming:
    armed: bool = False
    account_id: str | None = None
    capital_cap: Decimal = Decimal(0)
    armed_by: str | None = None
    armed_at: datetime | None = None


@dataclass
class Managed:
    deployment_id: str
    instrument_id: str
    candidate: str
    label: str
    signal: str
    mode: str  # PAPER | LIVE
    capital: Decimal
    parameters: dict[str, Any]
    created_at: datetime
    expected: dict[str, Any] = field(default_factory=dict)  # validation statistics at selection
    model: str | None = None
    source_deployment: str | None = None  # the paper deployment a live one was promoted from
    status: str = "ACTIVE"  # ACTIVE | CLOSING | RETIRED
    peak_pnl: Decimal = Decimal(0)
    pnl: Decimal = Decimal(0)
    ready_for_live_noted: bool = False
    live_deployment: str | None = None


@dataclass
class Decision:
    decision_id: str
    at: datetime
    kind: str  # DEPLOY | KEEP | RETIRE | PROMOTE | READY_FOR_LIVE | SKIP | ARM | DISARM | CYCLE | ERROR
    title: str
    reasons: list[str] = field(default_factory=list)
    instrument_id: str | None = None
    deployment_id: str | None = None
    run_id: str | None = None
    actor: str = AUTOPILOT


@dataclass
class Run:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    data_source: str = ""
    trials: int = 0
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    selected: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    summary: str | None = None
    error: str | None = None


Summarizer = Callable[[dict[str, Any]], str | None]
CandleLoader = Callable[[Instrument, int, int], list[Candle] | None]


class Autopilot:
    def __init__(
        self,
        platform: Platform,
        store: Store,
        audit: AuditLog,
        *,
        runner: Any = None,
        models: Any = None,
        venue_candles: CandleLoader | None = None,
        live_ready: Callable[[str], bool] | None = None,
        summarizer: Summarizer | None = None,
        calendar: NseCalendar | None = None,
    ):
        self._p = platform
        self._store = store
        self._audit = audit
        self._runner = runner
        self._models = models
        self._venue_candles = venue_candles
        self._live_ready = live_ready or (lambda account_id: True)
        self._summarizer = summarizer
        self.calendar = calendar or NseCalendar()
        self.config = decode(AutopilotConfig, store.get(KIND, "config") or encode(AutopilotConfig()))
        self.live = decode(LiveArming, store.get(KIND, "live") or encode(LiveArming()))
        self.managed: dict[str, Managed] = {
            m.deployment_id: m for m in (decode(Managed, d) for d in store.all(f"{KIND}_managed"))
        }
        self.last_run_at: datetime | None = None
        state = store.get(KIND, "state") or {}
        if state.get("last_run_at"):
            self.last_run_at = datetime.fromisoformat(state["last_run_at"])
        self.progress: dict[str, Any] = {"running": False}
        self._decision_seq = store.query("SELECT COUNT(*) AS n FROM documents WHERE kind = ?", (DECISIONS,))[
            0
        ]["n"]
        self._cycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        with platform.lock:
            if PAPER_AI_ACCOUNT not in platform.trading.accounts:
                platform.trading.register_account(
                    TradingAccount(PAPER_AI_ACCOUNT, "AI paper", "PAPER", AccountMode.PAPER, "INR")
                )

    # ---- configuration ------------------------------------------------------------------------

    def update_config(self, changes: dict[str, Any], actor: str) -> AutopilotConfig:
        merged = {**encode(self.config), **changes}
        try:
            config = decode(AutopilotConfig, merged)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValidationError(
                "AUTOPILOT_CONFIG_INVALID", [{"field": "config", "message": str(exc)}]
            ) from None
        problems = []
        for instrument_id in config.universe:
            try:
                self._p.instruments.get(instrument_id)
            except PlatformError:
                problems.append({"field": "universe", "message": f"unknown instrument {instrument_id}"})
        if config.capital <= 0:
            problems.append({"field": "capital", "message": "must be positive"})
        if not 0 < config.max_weight <= 1:
            problems.append({"field": "max_weight", "message": "between 0 and 1"})
        if config.data_source not in ("auto", "venue", "synthetic"):
            problems.append({"field": "data_source", "message": "auto, venue or synthetic"})
        if config.history_bars < 300:
            problems.append({"field": "history_bars", "message": "at least 300 bars"})
        if problems:
            raise ValidationError("AUTOPILOT_CONFIG_INVALID", problems)
        self.config = config
        self._store.put(KIND, "config", encode(config))
        self._audit.record(actor=actor, action="autopilot.configure", category="CONFIGURATION", data=changes)
        return config

    def arm_live(self, account_id: str, capital_cap: Decimal, actor: str) -> LiveArming:
        account = self._p.trading.get_account(account_id)
        if account.mode is not AccountMode.LIVE:
            raise PlatformError("ACCOUNT_NOT_LIVE", "choose a live broker account")
        if capital_cap <= 0:
            raise ValidationError(
                "CAPITAL_CAP_INVALID", [{"field": "capital_cap", "message": "must be positive"}]
            )
        self.live = LiveArming(True, account_id, capital_cap, actor, self._p.clock.now())
        self._store.put(KIND, "live", encode(self.live))
        self._decide(
            "ARM",
            f"Live trading armed on {account_id} with a cap of {capital_cap:,}",
            ["Paper deployments that meet the track-record rules can now be promoted to real orders."],
            actor=actor,
        )
        return self.live

    def disarm_live(self, actor: str, *, flatten: bool = True) -> LiveArming:
        """Stop promoting and close out every live autopilot deployment."""
        self.live = LiveArming()
        self._store.put(KIND, "live", encode(self.live))
        closed = []
        with self._p.lock:
            for m in self.managed.values():
                if m.mode == "LIVE" and m.status == "ACTIVE":
                    self._close(m, "live trading disarmed", flatten=flatten)
                    closed.append(m.instrument_id)
        self._decide(
            "DISARM",
            "Live trading disarmed",
            [f"Closed live deployments: {', '.join(closed)}" if closed else "No live deployments were open."],
            actor=actor,
        )
        return self.live

    # ---- scheduling ---------------------------------------------------------------------------

    def next_run_at(self) -> datetime | None:
        if not self.config.enabled:
            return None
        now = self._p.clock.now()
        if self.last_run_at is None:
            return now
        if self.config.interval_seconds >= 86400:
            # Daily bars: research once per trading day, after the close.
            day = self.last_run_at.astimezone(IST).date()
            while True:
                day += timedelta(days=1)
                if self.calendar.is_trading_day(day):
                    return datetime.combine(day, self.calendar.close_time, IST) + timedelta(minutes=30)
        return self.last_run_at + timedelta(hours=self.config.cycle_hours)

    def start(self, interval: float = 30.0) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, args=(interval,), name="autopilot", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception:
                log.exception("autopilot tick failed")

    def tick(self) -> None:
        self.monitor()
        due = self.next_run_at()
        if due is not None and due <= self._p.clock.now() and not self.progress["running"]:
            self.run_cycle()

    def run_in_background(self) -> bool:
        if self.progress["running"]:
            return False
        threading.Thread(target=self._safe_cycle, name="autopilot-cycle", daemon=True).start()
        return True

    def _safe_cycle(self) -> None:
        try:
            self.run_cycle()
        except Exception:
            log.exception("autopilot cycle failed")

    # ---- the cycle ----------------------------------------------------------------------------

    def run_cycle(self) -> Run:
        if not self._cycle_lock.acquire(blocking=False):
            raise PlatformError("AUTOPILOT_BUSY", "a research cycle is already running")
        run = Run(run_id=uuid.uuid4().hex[:12], started_at=self._p.clock.now())
        self.progress = {
            "running": True,
            "run_id": run.run_id,
            "done": 0,
            "total": 0,
            "message": "loading data",
        }
        try:
            self._cycle(run)
        except Exception as exc:
            run.error = str(exc)
            self._decide("ERROR", "Research cycle failed", [str(exc)], run_id=run.run_id)
            log.exception("autopilot cycle failed")
        finally:
            run.finished_at = self._p.clock.now()
            self.last_run_at = run.started_at
            self._store.put(KIND, "state", {"last_run_at": run.started_at.isoformat()})
            self._store.put(RUNS, run.run_id, encode(run))
            self.progress = {"running": False, "run_id": run.run_id}
            self._cycle_lock.release()
        return run

    def _cycle(self, run: Run) -> None:
        config = self.config
        if not config.universe:
            raise PlatformError("AUTOPILOT_NO_UNIVERSE", "choose the instruments the autopilot may trade")
        instruments = [self._p.instruments.get(i) for i in config.universe]
        candles, sources = {}, set()
        for instrument in instruments:
            series, source = self._load(instrument)
            candles[instrument.instrument_id] = series
            sources.add(source)
        run.data_source = "+".join(sorted(sources))

        def progress(done: int, total: int, message: str) -> None:
            self.progress.update(done=done, total=total, message=message)

        result = research(instruments, candles, config.research_config(), progress=progress)
        run.trials, run.skipped = result.trials, result.skipped
        ranked = sorted(result.evaluations, key=lambda e: e.score, reverse=True)
        run.leaderboard = [e.summary() for e in ranked[:60]]
        run.selected = [
            {
                **s.evaluation.summary(),
                "weight": s.weight,
                "capital": str(s.capital),
                "equity": _downsample(s.evaluation.equity, 200),
            }
            for s in result.selections
        ]

        with self._p.lock:
            self._review(result, run)
            self._deploy(result, candles, run)
            self._promote(run)
        self._decide(
            "CYCLE",
            f"Research cycle: {result.trials} strategies tested, {sum(e.passed for e in result.evaluations)} "
            f"passed, {len(result.selections)} selected",
            [f"Data: {run.data_source}"] + [f"Skipped {k}: {v}" for k, v in list(result.skipped.items())[:5]],
            run_id=run.run_id,
        )
        if config.explain_with_claude and self._summarizer is not None:
            run.summary = self._summarizer(self._briefing(run))

    def _load(self, instrument: Instrument) -> tuple[list[Candle], str]:
        config = self.config
        if config.data_source in ("auto", "venue") and self._venue_candles is not None:
            series = self._venue_candles(instrument, config.interval_seconds, config.history_bars)
            if series:
                return series, "broker history"
            if config.data_source == "venue":
                raise PlatformError("DATA_UNAVAILABLE", f"no broker history for {instrument.instrument_id}")
        return self._synthetic(instrument), "synthetic demo data"

    def _synthetic(self, instrument: Instrument) -> list[Candle]:
        config = self.config
        seed = int(hashlib.sha256(instrument.instrument_id.encode()).hexdigest()[:8], 16)
        drift = ((seed % 21) - 8) / 10_000  # a per-instrument tilt so some series trend and some do not
        interval = config.interval_seconds
        end = self._p.clock.now()
        start = end - timedelta(seconds=interval * config.history_bars)
        start = start.replace(microsecond=0)
        reference = self._p.market.reference_price(instrument.instrument_id) or Decimal(1000)
        return random_walk_candles(
            instrument,
            start,
            config.history_bars,
            interval_seconds=interval,
            start_price=reference,
            volatility=0.015,
            drift=drift,
            seed=seed,
        )

    # ---- acting on research -------------------------------------------------------------------

    def _review(self, result: ResearchResult, run: Run) -> None:
        by_key = {(e.instrument_id, e.candidate.key): e for e in result.evaluations}
        for m in list(self.managed.values()):
            if m.status != "ACTIVE" or m.mode != "PAPER":
                continue
            if m.instrument_id not in self.config.universe:
                self._retire(m, ["the instrument was removed from the autopilot universe"], run)
                continue
            e = by_key.get((m.instrument_id, m.candidate))
            if e is None:
                continue  # not re-tested this cycle (e.g. data gap): keep running
            if e.passed:
                m.expected = e.validation
                self._save(m)
                self._decide(
                    "KEEP",
                    f"Keeping {m.label} on {m.instrument_id}",
                    [_evidence(e)],
                    instrument_id=m.instrument_id,
                    deployment_id=m.deployment_id,
                    run_id=run.run_id,
                )
            else:
                self._retire(m, ["its edge no longer holds up on fresh data:", *e.reasons], run)

    def _deploy(self, result: ResearchResult, candles: dict[str, list[Candle]], run: Run) -> None:
        active = {
            m.instrument_id for m in self.managed.values() if m.status == "ACTIVE" and m.mode == "PAPER"
        }
        used = sum(
            (m.capital for m in self.managed.values() if m.status == "ACTIVE" and m.mode == "PAPER"),
            Decimal(0),
        )
        for selection in result.selections:
            e = selection.evaluation
            if e.instrument_id in active:
                continue  # one autopilot strategy per instrument; the incumbent passed review
            capital = min(selection.capital, self.config.capital - used)
            if capital <= 0:
                self._decide(
                    "SKIP",
                    f"No capital left for {e.candidate.label} on {e.instrument_id}",
                    ["the paper budget is fully allocated"],
                    instrument_id=e.instrument_id,
                    run_id=run.run_id,
                )
                continue
            # Try the selected strategy, then the instrument's other passing strategies in rank order.
            alternatives = sorted(
                (x for x in result.evaluations if x.passed and x.instrument_id == e.instrument_id),
                key=lambda x: x.score,
                reverse=True,
            )
            managed = None
            for candidate in alternatives:
                try:
                    managed = self._start_paper(candidate, capital, candles[e.instrument_id], run)
                except PlatformError as exc:
                    self._decide(
                        "SKIP",
                        f"Could not deploy {candidate.candidate.label} on {e.instrument_id}",
                        [exc.message],
                        instrument_id=e.instrument_id,
                        run_id=run.run_id,
                    )
                    if exc.code == "INSTRUMENT_CONFLICT":
                        break  # the instrument is taken; no alternative can help
                    continue
                e = candidate
                break
            if managed is None:
                continue
            used += capital
            self._decide(
                "DEPLOY",
                f"Paper trading {e.candidate.label} on {e.instrument_id} with {capital:,}",
                [_evidence(e), f"weight {selection.weight:.0%} of the budget from risk-parity allocation"],
                instrument_id=e.instrument_id,
                deployment_id=managed.deployment_id,
                run_id=run.run_id,
            )

    def _start_paper(self, e: Evaluation, capital: Decimal, series: list[Candle], run: Run) -> Managed:
        params = strategy_parameters(e.candidate, capital, self.config.research_config())
        model = None
        if e.candidate.signal == "ml":
            model = self._register_model(e.candidate, e.instrument_id, series, run)
            params["model"] = model
        trading = self._p.trading
        deployment = trading.create_deployment(
            strategy_name="autopilot",
            strategy_version="1.0.0",
            account_id=PAPER_AI_ACCOUNT,
            parameters=params,
            instruments=[e.instrument_id],
            created_by=AUTOPILOT,
            bar_interval_seconds=self.config.interval_seconds,
        )
        trading.approve(deployment.deployment_id, AUTOPILOT)
        trading.start(deployment.deployment_id)
        self._sync(deployment)
        managed = Managed(
            deployment.deployment_id,
            e.instrument_id,
            e.candidate.key,
            e.candidate.label,
            e.candidate.signal,
            "PAPER",
            capital,
            params,
            self._p.clock.now(),
            expected=e.validation,
            model=model,
        )
        self._save(managed)
        return managed

    def _register_model(
        self, candidate: Candidate, instrument_id: str, series: list[Candle], run: Run
    ) -> str:
        if self._models is None:
            raise PlatformError("MODELS_UNAVAILABLE", "the model registry is not available")
        from jdquant.ai.features import DEFAULT_FEATURES, build_feature_set
        from jdquant.ai.models import Stage

        feature_set = build_feature_set("autopilot-features", list(DEFAULT_FEATURES))
        model, report = train(
            feature_set, series, TrainingConfig(horizon=candidate.horizon, test_fraction=0.2)
        )
        name = f"ap-{instrument_id.split(':', 1)[1].lower()}-h{candidate.horizon}-{run.run_id[:6]}"
        mv = self._models.register(
            name, model, feature_set, report, created_by="autopilot-research", instrument_id=instrument_id
        )
        for stage in (Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION):
            self._models.promote(
                name, mv.version, stage, approver=AUTOPILOT, reason=f"autopilot run {run.run_id}"
            )
        return name

    def _promote(self, run: Run) -> None:
        now = self._p.clock.now()
        for m in list(self.managed.values()):
            if m.mode != "PAPER" or m.status != "ACTIVE" or m.live_deployment:
                continue
            ok, reasons = self._track_record(m, now)
            if not ok:
                continue
            if not self.live.armed or not self.live.account_id:
                if not m.ready_for_live_noted:
                    m.ready_for_live_noted = True
                    self._save(m)
                    self._decide(
                        "READY_FOR_LIVE",
                        f"{m.label} on {m.instrument_id} is ready for live trading",
                        [
                            *reasons,
                            "Arm live trading with a capital cap to let the autopilot place real orders.",
                        ],
                        instrument_id=m.instrument_id,
                        deployment_id=m.deployment_id,
                        run_id=run.run_id,
                    )
                continue
            if not self._live_ready(self.live.account_id):
                self._decide(
                    "SKIP",
                    f"Live promotion of {m.instrument_id} waits for the broker",
                    ["the broker connection is not signed in"],
                    instrument_id=m.instrument_id,
                    run_id=run.run_id,
                )
                continue
            used = sum(
                (x.capital for x in self.managed.values() if x.mode == "LIVE" and x.status == "ACTIVE"),
                Decimal(0),
            )
            capital = min(m.capital, self.live.capital_cap - used)
            if capital <= 0:
                continue
            try:
                live = self._start_live(m, capital)
            except PlatformError as exc:
                self._decide(
                    "SKIP",
                    f"Could not promote {m.instrument_id} to live",
                    [exc.message],
                    instrument_id=m.instrument_id,
                    run_id=run.run_id,
                )
                continue
            self._decide(
                "PROMOTE",
                f"Live trading {m.label} on {m.instrument_id} with {capital:,}",
                [*reasons, f"within the live cap of {self.live.capital_cap:,} set by {self.live.armed_by}"],
                instrument_id=m.instrument_id,
                deployment_id=live.deployment_id,
                run_id=run.run_id,
            )

    def _track_record(self, m: Managed, now: datetime) -> tuple[bool, list[str]]:
        days = (now - m.created_at).days
        trades = sum(1 for f in self._p.oms.fills if f.deployment_id == m.deployment_id and f.side.sign > 0)
        self._mark(m)
        drawdown = (m.peak_pnl - m.pnl) / m.capital if m.capital else Decimal(0)
        if days < self.config.min_paper_days or trades < self.config.min_paper_trades:
            return False, []
        if m.pnl < -m.capital * Decimal("0.02") or drawdown > Decimal(
            str(self.config.max_deployment_drawdown / 2)
        ):
            return False, []
        return True, [
            f"{days} days and {trades} trades on paper",
            f"paper P&L {m.pnl:,.2f} on {m.capital:,} allocated, drawdown {drawdown:.1%}",
        ]

    def _start_live(self, m: Managed, capital: Decimal) -> Managed:
        params = {**m.parameters, "capital": str(capital)}
        trading = self._p.trading
        deployment = trading.create_deployment(
            strategy_name="autopilot",
            strategy_version="1.0.0",
            account_id=self.live.account_id,
            parameters=params,
            instruments=[m.instrument_id],
            created_by=AUTOPILOT,
            bar_interval_seconds=self.config.interval_seconds,
        )
        # The person who armed live trading is the approver of record for every live autopilot deployment.
        trading.approve(deployment.deployment_id, self.live.armed_by or AUTOPILOT)
        trading.start(deployment.deployment_id)
        self._sync(deployment)
        live = Managed(
            deployment.deployment_id,
            m.instrument_id,
            m.candidate,
            m.label,
            m.signal,
            "LIVE",
            capital,
            params,
            self._p.clock.now(),
            expected=m.expected,
            model=m.model,
            source_deployment=m.deployment_id,
        )
        m.live_deployment = deployment.deployment_id
        self._save(live)
        self._save(m)
        return live

    # ---- monitoring ---------------------------------------------------------------------------

    def monitor(self) -> None:
        """Mark P&L, close out deployments that breach their drawdown limit, finish close-outs."""
        limit = Decimal(str(self.config.max_deployment_drawdown))
        with self._p.lock:
            for m in list(self.managed.values()):
                deployment = self._p.trading.deployments.get(m.deployment_id)
                if deployment is None:
                    continue
                if m.status == "CLOSING":
                    self._finish_close(m, deployment)
                    continue
                if m.status != "ACTIVE":
                    continue
                self._mark(m)
                if deployment.state in (DeploymentState.FAILED, DeploymentState.HALTED):
                    self._retire(
                        m, [f"the deployment {deployment.state.value.lower()}: {deployment.state_reason}"]
                    )
                    continue
                drawdown = (m.peak_pnl - m.pnl) / m.capital if m.capital else Decimal(0)
                if drawdown > limit:
                    self._retire(
                        m, [f"drawdown {drawdown:.1%} of allocated capital exceeded the {limit:.0%} limit"]
                    )

    def _mark(self, m: Managed) -> None:
        pnl = Decimal(0)
        for p in self._p.positions.positions():
            if p.deployment_id != m.deployment_id:
                continue
            pnl += p.realized_pnl - p.fees_paid
            price = self._p.market.reference_price(p.instrument_id)
            if p.quantity and price is not None:
                pnl += p.unrealized_pnl(price)
        m.pnl = pnl
        if pnl > m.peak_pnl:
            m.peak_pnl = pnl
        self._save(m)

    def _retire(self, m: Managed, reasons: list[str], run: Run | None = None) -> None:
        self._close(m, reasons[0])
        self._decide(
            "RETIRE",
            f"Closing {m.label} on {m.instrument_id} ({m.mode.lower()})",
            reasons,
            instrument_id=m.instrument_id,
            deployment_id=m.deployment_id,
            run_id=run.run_id if run else None,
        )
        if m.live_deployment and m.live_deployment in self.managed:
            live = self.managed[m.live_deployment]
            if live.status == "ACTIVE":
                self._retire(live, ["its paper strategy was retired", *reasons], run)

    def _close(self, m: Managed, reason: str, flatten: bool = True) -> None:
        trading = self._p.trading
        deployment = trading.deployments.get(m.deployment_id)
        m.status = "CLOSING"
        if deployment is not None:
            try:
                if deployment.state in (DeploymentState.HALTED, DeploymentState.FAILED):
                    trading.stop(m.deployment_id)
                has_position = any(
                    p.is_open for p in self._p.positions.positions(deployment_id=m.deployment_id)
                )
                if flatten and has_position and deployment.state is not DeploymentState.FLATTENING:
                    trading.flatten(m.deployment_id)  # sells at market; STOPPED once flat
                elif deployment.state in (DeploymentState.RUNNING, DeploymentState.PAUSED):
                    trading.stop(m.deployment_id)
                self._sync(deployment)
            except PlatformError:
                log.exception("closing %s failed", m.deployment_id)
            self._finish_close(m, deployment)
        self._save(m)

    def _finish_close(self, m: Managed, deployment) -> None:
        trading = self._p.trading
        try:
            if deployment.state in (DeploymentState.STOPPED, DeploymentState.READY):
                trading.retire(m.deployment_id)
                self._sync(deployment)
            if deployment.state is DeploymentState.RETIRED:
                m.status = "RETIRED"
                self._save(m)
        except PlatformError:
            log.exception("retiring %s failed", m.deployment_id)

    # ---- records --------------------------------------------------------------------------------

    def decisions(self, limit: int = 100) -> list[Decision]:
        rows = self._store.query(
            "SELECT data FROM documents WHERE kind = ? ORDER BY id DESC LIMIT ?", (DECISIONS, limit)
        )
        return [decode(Decision, json.loads(r["data"])) for r in rows]

    def runs(self, limit: int = 20) -> list[Run]:
        runs = [decode(Run, d) for d in self._store.all(RUNS)]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)[:limit]

    def status(self) -> dict[str, Any]:
        return {
            "config": encode(self.config),
            "live": encode(self.live),
            "progress": dict(self.progress),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": (n.isoformat() if (n := self.next_run_at()) else None),
            "paper_account": PAPER_AI_ACCOUNT,
            "managed": [
                encode(m) for m in sorted(self.managed.values(), key=lambda m: m.created_at, reverse=True)
            ],
        }

    def _decide(
        self, kind: str, title: str, reasons: list[str], *, actor: str = AUTOPILOT, **refs: Any
    ) -> None:
        now = self._p.clock.now()
        self._decision_seq += 1
        decision = Decision(
            f"{now.strftime('%Y%m%d%H%M%S%f')}-{self._decision_seq:08d}",
            now,
            kind,
            title,
            reasons,
            actor=actor,
            **refs,
        )
        self._store.put(DECISIONS, decision.decision_id, encode(decision))
        self._audit.record(
            actor=actor,
            action=f"autopilot.{kind.lower()}",
            category="TRADING",
            target=refs.get("deployment_id") or refs.get("instrument_id"),
            data={"title": title, "reasons": reasons},
        )

    def _save(self, m: Managed) -> None:
        self.managed[m.deployment_id] = m
        self._store.put(f"{KIND}_managed", m.deployment_id, encode(m))

    def _sync(self, deployment) -> None:
        if self._runner is not None:
            self._runner.sync(deployment)

    def _briefing(self, run: Run) -> dict[str, Any]:
        decisions = [encode(d) for d in self.decisions(40) if d.run_id == run.run_id]
        return {
            "data_source": run.data_source,
            "strategies_tested": run.trials,
            "selected": [
                {k: s[k] for k in ("instrument_id", "label", "validation", "holdout", "dsr", "capital")}
                for s in run.selected
            ],
            "best_rejected": [
                {k: e[k] for k in ("instrument_id", "label", "reasons")}
                for e in run.leaderboard
                if not e["passed"]
            ][:5],
            "decisions": [{k: d[k] for k in ("kind", "title", "reasons")} for d in decisions],
            "live_trading_armed": self.live.armed,
        }


def _evidence(e: Evaluation) -> str:
    v, h = e.validation, e.holdout
    return (
        f"out-of-sample: {v['return']:+.1%} return, Sharpe {v['sharpe']:.2f}, "
        f"max drawdown {v['max_drawdown']:.1%}, {v['trades']} trades; holdout {h['return']:+.1%}; "
        f"luck-adjusted confidence {e.dsr:.0%}"
    )


def _downsample(points: list[tuple[datetime, float]], limit: int) -> list[list[Any]]:
    step = max(1, len(points) // limit)
    sampled = points[::step]
    if points and sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return [[t.isoformat(), round(v, 5)] for t, v in sampled]


def make_summarizer(copilot: Any) -> Summarizer:
    """Plain-language briefing of a cycle from Claude; returns None if Claude is not configured or fails."""
    from jdquant.ai.copilot import FALLBACK_BETA
    from jdquant.ai.prompts import AUTOPILOT_SUMMARY

    def summarize(briefing: dict[str, Any]) -> str | None:
        started = time.monotonic()
        try:
            client = copilot._ensure_client()
            response = client.beta.messages.create(
                model=copilot.model,
                max_tokens=2000,
                system=AUTOPILOT_SUMMARY.text,
                messages=[{"role": "user", "content": json.dumps(briefing, default=str)}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            log.info("autopilot summary unavailable: %s", exc)
            return None
        usage = getattr(response, "usage", None)
        copilot.calls.record(
            user_id=AUTOPILOT,
            template=AUTOPILOT_SUMMARY,
            model=copilot.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome=getattr(response, "stop_reason", None) or "unknown",
        )
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return text.strip() or None

    return summarize
