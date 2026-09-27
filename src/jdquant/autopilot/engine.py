"""The autopilot: research, deploy, monitor, retire and promote without a human in the loop.

Each cycle it loads history for the configured universe, runs walk-forward research, and acts on the result:
new winners start trading on the autopilot's own paper account, deployments whose edge no longer holds up
are closed out, and paper deployments with a clean track record move to a live broker account — but only
while a person has armed that account with a capital cap. Every action is recorded as a decision with its
reasons. Risk limits and kill switches apply to everything it does, exactly as for manual trading.

It runs the book the way a systematic fund would: positions sized by volatility, one strategy per
instrument, futures rolled before expiry, and the whole book cut back to cash when its drawdown from the
peak exceeds a limit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.ai.analyst import Analyst, AnalystReport
from jdquant.ai.training import TrainingConfig, train
from jdquant.autopilot.research import (
    Candidate,
    Evaluation,
    ResearchConfig,
    ResearchResult,
    research,
    strategy_name,
    strategy_parameters,
)
from jdquant.autopilot.strategy import AutopilotStrategy, RotationStrategy
from jdquant.connectivity.connections import markets_of
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.instruments import Instrument
from jdquant.marketdata.live import annual_volatility
from jdquant.marketdata.records import Candle
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.markets.india import NseCalendar, Product
from jdquant.markets.sessions import FX_VENUES, session_for
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.platform import DEMO_PRICES, Platform
from jdquant.risk.engine import BreachAction, LimitType, RiskLimit, RiskProfile, Scope
from jdquant.security.audit import AuditLog
from jdquant.trading.engine import AccountMode, DeploymentState, TradingAccount

log = logging.getLogger(__name__)

AUTOPILOT = "autopilot"
PAPER_AI_ACCOUNT = "paper-ai"
KIND = "autopilot"
DECISIONS = "autopilot_decision"
RUNS = "autopilot_run"
ACTIVE = (DeploymentState.RUNNING, DeploymentState.PAUSED, DeploymentState.READY)
VERSIONS = {"autopilot": AutopilotStrategy.version, "rotation": RotationStrategy.version}
# INR per unit of each quote currency. Where the platform has a live price for the pair (e.g. OANDA
# USD_JPY), the rate is derived from it instead; otherwise these defaults apply until edited.
DEFAULT_FX = {
    "INR": Decimal(1),
    "USD": Decimal(85),
    "USDT": Decimal(85),
    "USDC": Decimal(85),
    "EUR": Decimal("99.5"),
    "GBP": Decimal("115"),
    "JPY": Decimal("0.58"),
    "CHF": Decimal("106"),
    "AUD": Decimal("56"),
    "CAD": Decimal("61.5"),
    "NZD": Decimal("51"),
}


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
    trailing_stop: Decimal = Decimal(0)  # exit this far below the high since entry; 0 disables
    slippage_bps: Decimal = Decimal(5)
    min_sharpe: float = 0.5
    max_drawdown: float = 0.25
    min_dsr: float = 0.75
    min_trades: int = 5
    cycle_hours: float = 24.0  # for intraday bars; daily bars research after each close
    max_deployment_drawdown: float = 0.15  # of allocated capital, then the deployment is closed out
    min_paper_days: int = 10
    min_paper_trades: int = 2
    use_analyst: bool = True  # an LLM reviews each cycle and writes the briefing
    analyst_can_veto: bool = False  # a high-severity analyst concern blocks that strategy (never adds risk)
    # Capital protection. The book may lose at most `loss_floor` of its budget; once it is in profit the
    # floor rises to keep `lock_in_gains` of the peak gain. New positions shrink as the cushion above the
    # floor shrinks, and at the floor everything goes to cash until the owner resets protection.
    loss_floor: float = 0.10
    lock_in_gains: float = 0.5
    daily_loss_limit: float = 0.03  # of the budget per account per day, enforced by the risk engine
    max_cost_share: float = 0.5  # reject strategies whose charges eat more of the gross profit than this
    vol_target: Decimal = Decimal("0.2")  # annualized volatility each position is sized to
    max_participation: float = 0.01  # largest position as a share of daily traded value (real data only)
    portfolio_drawdown_limit: float = 0.10  # of the budget: cut the whole book to cash beyond this
    halt_cooldown_days: int = 5
    roll_days: int = 3  # roll futures this many days before expiry
    # INR per unit of other quote currencies, to size crypto in USDT from an INR budget. Update as rates move.
    fx_rates: dict[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_FX))
    # AI trade monitor: an LLM reviews every open autopilot position and proposed entry each minute.
    monitor_enabled: bool = True
    monitor_mode: str = "advise"  # advise: record verdicts only | act: close, halve or hold back trades
    monitor_interval_seconds: int = 60
    monitor_min_confidence: float = 0.7  # verdicts below this confidence are never acted on
    monitor_entry_gate: bool = True  # in act mode, entries wait for the model's approval
    monitor_fallback: str = "allow"  # when the model does not answer: allow | block entries
    monitor_cooldown_minutes: int = 60  # after an AI exit, no re-entry on that instrument for this long
    monitor_max_calls_per_day: int = 1500
    monitor_reasoning: bool = True  # let reasoning models think before answering (slower)

    def research_config(
        self, real_data: bool = True, fx_rates: dict[str, Decimal] | None = None
    ) -> ResearchConfig:
        return ResearchConfig(
            max_cost_share=self.max_cost_share,
            vol_target=self.vol_target,
            max_participation=self.max_participation if real_data else 0.0,
            fx_rates=fx_rates or {**DEFAULT_FX, **self.fx_rates},
            interval_seconds=self.interval_seconds,
            capital=self.capital,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            trailing_stop=self.trailing_stop,
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
class ArmedAccount:
    account_id: str
    capital_cap: Decimal  # in the budget currency (INR)
    armed_by: str
    armed_at: datetime
    allow_futures: bool = False  # futures quantities are in lots; confirm with a test trade first


@dataclass
class LiveArming:
    accounts: dict[str, ArmedAccount] = field(default_factory=dict)

    @property
    def armed(self) -> bool:
        return bool(self.accounts)


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
    pnl: Decimal = Decimal(0)  # in the budget currency
    ready_for_live_noted: bool = False
    live_deployment: str | None = None
    account_id: str = PAPER_AI_ACCOUNT
    universe_id: str = ""  # the universe entry it came from (e.g. MCX:GOLDM1! for a rolling future)
    instruments: list[str] = field(default_factory=list)  # a basket for rotation strategies
    currency: str = "INR"  # quote currency of its instruments
    strategy: str = "autopilot"
    rolled_to: str | None = None

    def __post_init__(self) -> None:
        self.instruments = self.instruments or [self.instrument_id]
        self.universe_id = self.universe_id or self.instrument_id


@dataclass
class Decision:
    decision_id: str
    at: datetime
    # DEPLOY | KEEP | RETIRE | ROLL | HALT | PROMOTE | READY_FOR_LIVE | SKIP | ARM | DISARM | CYCLE | ERROR
    kind: str
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
    analyst: str | None = None  # provider and model that reviewed the cycle
    concerns: list[dict[str, Any]] = field(default_factory=list)


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
        analyst: Analyst | None = None,
        calendar: NseCalendar | None = None,
    ):
        self._p = platform
        self._store = store
        self._audit = audit
        self._runner = runner
        self._models = models
        self._venue_candles = venue_candles
        self._live_ready = live_ready or (lambda account_id: True)
        self._analyst = analyst
        self.calendar = calendar or NseCalendar()
        self.config = decode(AutopilotConfig, store.get(KIND, "config") or encode(AutopilotConfig()))
        self.live = self._load_live(store.get(KIND, "live") or {})
        self.managed: dict[str, Managed] = {
            m.deployment_id: m for m in (decode(Managed, d) for d in store.all(f"{KIND}_managed"))
        }
        for m in self.managed.values():  # records written before accounts were tracked
            deployment = platform.trading.deployments.get(m.deployment_id)
            if deployment is not None and m.mode == "LIVE" and m.account_id == PAPER_AI_ACCOUNT:
                m.account_id = deployment.account_id
        self.last_run_at: datetime | None = None
        state = store.get(KIND, "state") or {}
        if state.get("last_run_at"):
            self.last_run_at = datetime.fromisoformat(state["last_run_at"])
        self.peaks: dict[str, Decimal] = {k: Decimal(v) for k, v in state.get("peaks", {}).items()}
        self.halted_until: dict[str, datetime] = {
            k: datetime.fromisoformat(v) for k, v in state.get("halted_until", {}).items()
        }
        self.floor_hit: dict[str, bool] = dict(state.get("floor_hit", {}))
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
            self.apply_risk_limits()

    @staticmethod
    def _load_live(doc: dict[str, Any]) -> LiveArming:
        if "accounts" in doc:
            return decode(LiveArming, doc)
        if doc.get("armed") and doc.get("account_id"):  # the single-account format of earlier versions
            armed = ArmedAccount(
                doc["account_id"],
                Decimal(doc["capital_cap"]),
                doc.get("armed_by") or "unknown",
                datetime.fromisoformat(doc["armed_at"]),
            )
            return LiveArming({armed.account_id: armed})
        return LiveArming()

    def _save_state(self) -> None:
        self._store.put(
            KIND,
            "state",
            {
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "peaks": {k: str(v) for k, v in self.peaks.items()},
                "halted_until": {k: v.isoformat() for k, v in self.halted_until.items()},
                "floor_hit": self.floor_hit,
            },
        )

    def apply_risk_limits(self) -> None:
        """Daily loss limits on the autopilot's accounts, enforced order by order by the risk engine."""
        risk = self._p.risk
        accounts = {PAPER_AI_ACCOUNT: self.config.capital}
        accounts.update({a.account_id: a.capital_cap for a in self.live.accounts.values()})
        mine = [
            RiskProfile(
                f"autopilot:{account_id}",
                Scope.ACCOUNT,
                [
                    RiskLimit(
                        LimitType.MAX_DAILY_LOSS,
                        (budget * Decimal(str(self.config.daily_loss_limit))).quantize(Decimal(1)),
                        BreachAction.REDUCE_ONLY,
                    )
                ],
                target_id=account_id,
            )
            for account_id, budget in accounts.items()
            if budget > 0 and self.config.daily_loss_limit > 0
        ]
        risk.set_profiles([p for p in risk.profiles if not p.name.startswith("autopilot:")] + mine)

    # ---- universe -----------------------------------------------------------------------------

    def resolve(self, universe_id: str) -> Instrument | None:
        """An instrument, or for `VENUE:ROOT1!` the front futures contract not about to expire."""
        if not universe_id.endswith("1!"):
            try:
                return self._p.instruments.get(universe_id)
            except PlatformError:
                return None
        venue, root = universe_id[:-2].split(":", 1)
        return self._front(venue, root)

    def _front(self, venue: str, root: str, after: datetime | None = None) -> Instrument | None:
        cutoff = max(
            after or self._p.clock.now(), self._p.clock.now() + timedelta(days=self.config.roll_days)
        )
        contracts = [
            i
            for i in self._p.instruments.search()
            if i.venue == venue and i.underlying == root and i.expiry is not None and i.expiry > cutoff
        ]
        return min(contracts, key=lambda i: i.expiry) if contracts else None

    def universe_options(self) -> list[dict[str, Any]]:
        """What the universe may contain: instruments by asset group, futures as continuous front months."""
        from jdquant.markets.sessions import asset_group

        options, roots = [], set()
        for i in self._p.instruments.search():
            if i.is_future:
                roots.add((i.venue, i.underlying))
                continue
            options.append({"id": i.instrument_id, "label": i.instrument_id, "group": asset_group(i)})
        for venue, root in sorted(roots):
            front = self._front(venue, root)
            if front is not None:
                options.append(
                    {
                        "id": f"{venue}:{root}1!",
                        "label": f"{venue}:{root} front month ({front.symbol})",
                        "group": f"{asset_group(front)} futures",
                    }
                )
        return sorted(options, key=lambda o: (o["group"], o["id"]))

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
            if self.resolve(instrument_id) is None:
                problems.append({"field": "universe", "message": f"unknown instrument {instrument_id}"})
        for currency, rate in config.fx_rates.items():
            if rate <= 0:
                problems.append({"field": "fx_rates", "message": f"{currency} rate must be positive"})
        if config.capital <= 0:
            problems.append({"field": "capital", "message": "must be positive"})
        if not 0 < config.max_weight <= 1:
            problems.append({"field": "max_weight", "message": "between 0 and 1"})
        if config.data_source not in ("auto", "venue", "synthetic"):
            problems.append({"field": "data_source", "message": "auto, venue or synthetic"})
        if config.monitor_mode not in ("advise", "act"):
            problems.append({"field": "monitor_mode", "message": "advise or act"})
        if config.monitor_fallback not in ("allow", "block"):
            problems.append({"field": "monitor_fallback", "message": "allow or block"})
        if not 30 <= config.monitor_interval_seconds <= 3600:
            problems.append({"field": "monitor_interval_seconds", "message": "between 30 and 3600"})
        if not 0 <= config.monitor_min_confidence <= 1:
            problems.append({"field": "monitor_min_confidence", "message": "between 0 and 1"})
        if config.history_bars < 400:
            problems.append({"field": "history_bars", "message": "at least 400 bars"})
        if problems:
            raise ValidationError("AUTOPILOT_CONFIG_INVALID", problems)
        self.config = config
        self._store.put(KIND, "config", encode(config))
        with self._p.lock:
            self.apply_risk_limits()
        self._audit.record(actor=actor, action="autopilot.configure", category="CONFIGURATION", data=changes)
        return config

    def arm_live(
        self, account_id: str, capital_cap: Decimal, actor: str, *, allow_futures: bool = False
    ) -> LiveArming:
        account = self._p.trading.get_account(account_id)
        if account.mode is not AccountMode.LIVE:
            raise PlatformError("ACCOUNT_NOT_LIVE", "choose a live broker account")
        if capital_cap <= 0:
            raise ValidationError(
                "CAPITAL_CAP_INVALID", [{"field": "capital_cap", "message": "must be positive"}]
            )
        self.live.accounts[account_id] = ArmedAccount(
            account_id, capital_cap, actor, self._p.clock.now(), allow_futures
        )
        self._store.put(KIND, "live", encode(self.live))
        with self._p.lock:
            self.apply_risk_limits()
        reasons = ["Paper deployments that meet the track-record rules can now be promoted to real orders."]
        if allow_futures:
            reasons.append(
                "Futures are included: quantities are in lots — confirm one lot with a test trade."
            )
        self._decide(
            "ARM", f"Live trading armed on {account_id} with a cap of {capital_cap:,}", reasons, actor=actor
        )
        return self.live

    def disarm_live(self, actor: str, *, account_id: str | None = None, flatten: bool = True) -> LiveArming:
        """Stop promoting and close out the live autopilot deployments (one account or all)."""
        targets = [account_id] if account_id else list(self.live.accounts)
        for target in targets:
            self.live.accounts.pop(target, None)
        self._store.put(KIND, "live", encode(self.live))
        closed = []
        with self._p.lock:
            self.apply_risk_limits()
            for m in list(self.managed.values()):
                if (
                    m.mode == "LIVE"
                    and m.status == "ACTIVE"
                    and (account_id is None or m.account_id == account_id)
                ):
                    self._close(m, "live trading disarmed", flatten=flatten)
                    closed.append(m.instrument_id)
        self._decide(
            "DISARM",
            f"Live trading disarmed{f' on {account_id}' if account_id else ''}",
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
            # Daily bars: research once per trading day, 30 minutes after the last market in the
            # universe closes (NSE 15:30 IST, forex 17:00 New York, crypto midnight UTC).
            return max(self._next_close(s, self.last_run_at) for s in self._sessions()) + timedelta(
                minutes=30
            )
        return self.last_run_at + timedelta(hours=self.config.cycle_hours)

    def _sessions(self) -> set:
        sessions = set()
        for universe_id in self.config.universe:
            instrument = self.resolve(universe_id)
            if instrument is not None:
                sessions.add(session_for(instrument))
        return sessions or {self.calendar}

    @staticmethod
    def _next_close(session, after: datetime) -> datetime:
        day = after.astimezone(session.tz).date()
        while True:
            close = session.session_close(day)
            if close > after and session.is_trading_day(day):
                return close
            day += timedelta(days=1)

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
            self._save_state()
            self._store.put(RUNS, run.run_id, encode(run))
            self.progress = {"running": False, "run_id": run.run_id}
            self._cycle_lock.release()
        return run

    def _cycle(self, run: Run) -> None:
        config = self.config
        if not config.universe:
            raise PlatformError("AUTOPILOT_NO_UNIVERSE", "choose the instruments the autopilot may trade")
        instruments, unresolved = [], {}
        self._universe_of: dict[str, str] = {}
        for universe_id in config.universe:
            instrument = self.resolve(universe_id)
            if instrument is None:
                unresolved[universe_id] = "no tradable contract (expired or not loaded)"
                continue
            instruments.append(instrument)
            self._universe_of[instrument.instrument_id] = universe_id
        candles, sources = {}, set()
        for instrument in instruments:
            series, source = self._load(instrument)
            candles[instrument.instrument_id] = series
            sources.add(source)
        run.data_source = "+".join(sorted(sources))

        def progress(done: int, total: int, message: str) -> None:
            self.progress.update(done=done, total=total, message=message)

        real = sources == {"broker history"}
        research_config = config.research_config(real_data=real, fx_rates=self.fx_rates())
        result = research(instruments, candles, research_config, progress=progress)
        result.skipped.update(unresolved)
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

        if config.use_analyst and self._analyst is not None:
            self.progress.update(message="analyst reviewing the results")
            report = self._analyst(self._review_briefing(result, run))
            if report is not None:
                self._apply_report(report, result, run)

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

    def _apply_report(self, report: AnalystReport, result: ResearchResult, run: Run) -> None:
        run.analyst = f"{report.provider} · {report.model}"
        run.summary = report.summary or None
        run.concerns = [encode(c) for c in report.concerns]
        if not self.config.analyst_can_veto:
            return
        vetoed = {c.instrument_id: c.concern for c in report.concerns if c.severity == "high"}
        kept = []
        for selection in result.selections:
            e = selection.evaluation
            reason = vetoed.get(e.instrument_id)
            if reason is None:
                kept.append(selection)
                continue
            self._decide(
                "SKIP",
                f"Analyst vetoed {e.candidate.label} on {e.instrument_id}",
                [f"{report.provider} flagged a high-severity concern: {reason}"],
                instrument_id=e.instrument_id,
                run_id=run.run_id,
            )
        result.selections = kept

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
        reference = (
            self._p.market.reference_price(instrument.instrument_id)
            or DEMO_PRICES.get(instrument.instrument_id)
            or Decimal(1000)
        )
        volatility = 0.015
        if instrument.venue in FX_VENUES:  # quieter markets: scale to the bar size
            bars = session_for(instrument).bars_per_year(interval)
            volatility = annual_volatility(instrument) / math.sqrt(bars) * 2
            drift *= volatility / 0.015
        return random_walk_candles(
            instrument,
            start,
            config.history_bars,
            interval_seconds=interval,
            start_price=reference,
            volatility=volatility,
            drift=drift,
            seed=seed,
        )

    # ---- acting on research -------------------------------------------------------------------

    def _review(self, result: ResearchResult, run: Run) -> None:
        by_key = {(e.instrument_id, e.candidate.key): e for e in result.evaluations}
        for m in list(self.managed.values()):
            if m.status != "ACTIVE" or m.mode != "PAPER":
                continue
            if m.universe_id not in self.config.universe and not m.universe_id.startswith("PORTFOLIO:"):
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
        if self.floor_hit.get("PAPER"):
            self._decide(
                "SKIP",
                "New paper deployments stopped: the loss floor was reached",
                ["reset capital protection in the autopilot settings to trade again"],
                run_id=run.run_id,
            )
            return
        exposure = self.protection("PAPER")["exposure"]
        if self._halted("PAPER"):
            self._decide(
                "SKIP",
                "New paper deployments paused after a portfolio drawdown halt",
                [f"resumes after {self.halted_until['PAPER']:%Y-%m-%d %H:%M} UTC"],
                run_id=run.run_id,
            )
            return
        live_paper = [m for m in self.managed.values() if m.status == "ACTIVE" and m.mode == "PAPER"]
        covered = {i for m in live_paper for i in m.instruments}
        used = sum((m.capital for m in live_paper), Decimal(0))
        for selection in result.selections:
            e = selection.evaluation
            if covered & set(e.instruments):
                continue  # one autopilot strategy per instrument; the incumbent passed review
            capital = min((selection.capital * exposure).quantize(Decimal(1)), self.config.capital - used)
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
            ) or [e]
            managed = None
            for candidate in alternatives:
                try:
                    managed = self._start_paper(candidate, capital, candles, run)
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
            covered |= set(e.instruments)
            self._decide(
                "DEPLOY",
                f"Paper trading {e.candidate.label} on {e.instrument_id} with {capital:,}",
                [
                    _evidence(e),
                    f"weight {selection.weight:.0%} of the budget from risk-parity allocation"
                    + (f", scaled to {exposure:.0%} by the loss floor" if exposure < 1 else ""),
                ],
                instrument_id=e.instrument_id,
                deployment_id=managed.deployment_id,
                run_id=run.run_id,
            )

    def _start_paper(
        self, e: Evaluation, capital: Decimal, candles: dict[str, list[Candle]], run: Run
    ) -> Managed:
        """Deploy on the AI paper account; `capital` is in the budget currency."""
        lead = self._p.instruments.get(e.instruments[0])
        research_config = self.config.research_config()
        params = strategy_parameters(
            e.candidate, research_config.capital_for(lead, capital), research_config, lead
        )
        model = None
        if e.candidate.signal == "ml":
            model = self._register_model(e.candidate, e.instrument_id, candles[e.instrument_id], run)
            params["model"] = model
        name = strategy_name(e.candidate)
        deployment = self._launch(name, PAPER_AI_ACCOUNT, params, e.instruments, AUTOPILOT)
        universe_id = (
            e.instrument_id
            if e.is_portfolio
            else getattr(self, "_universe_of", {}).get(e.instrument_id, e.instrument_id)
        )
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
            universe_id=universe_id,
            instruments=list(e.instruments),
            currency=lead.quote_asset,
            strategy=name,
        )
        self._save(managed)
        return managed

    def _launch(
        self, strategy: str, account_id: str, params: dict[str, Any], instruments: list[str], approver: str
    ):
        trading = self._p.trading
        deployment = trading.create_deployment(
            strategy_name=strategy,
            strategy_version=VERSIONS[strategy],
            account_id=account_id,
            parameters=params,
            instruments=instruments,
            created_by=AUTOPILOT,
            bar_interval_seconds=self.config.interval_seconds,
        )
        trading.approve(deployment.deployment_id, approver)
        trading.start(deployment.deployment_id)
        self._sync(deployment)
        return deployment

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

    def _live_account_for(self, m: Managed) -> ArmedAccount | None:
        """An armed account whose broker trades every instrument of `m` (and allows futures if needed)."""
        needs_futures = any(self._p.instruments.get(i).is_future for i in m.instruments)
        for armed in self.live.accounts.values():
            try:
                account = self._p.trading.get_account(armed.account_id)
            except PlatformError:
                continue
            markets = markets_of(account.venue)
            if all(i.split(":", 1)[0] in markets for i in m.instruments) and (
                armed.allow_futures or not needs_futures
            ):
                return armed
        return None

    def _promote(self, run: Run) -> None:
        now = self._p.clock.now()
        if self._halted("LIVE") or self.floor_hit.get("LIVE"):
            return
        for m in list(self.managed.values()):
            if m.mode != "PAPER" or m.status != "ACTIVE" or m.live_deployment:
                continue
            ok, reasons = self._track_record(m, now)
            if not ok:
                continue
            armed = self._live_account_for(m)
            if armed is None:
                if not m.ready_for_live_noted:
                    m.ready_for_live_noted = True
                    self._save(m)
                    self._decide(
                        "READY_FOR_LIVE",
                        f"{m.label} on {m.instrument_id} is ready for live trading",
                        [
                            *reasons,
                            "Arm a broker account that trades it (with a capital cap) to let the autopilot "
                            "place real orders.",
                        ],
                        instrument_id=m.instrument_id,
                        deployment_id=m.deployment_id,
                        run_id=run.run_id,
                    )
                continue
            if not self._live_ready(armed.account_id):
                self._decide(
                    "SKIP",
                    f"Live promotion of {m.instrument_id} waits for the broker",
                    ["the broker connection is not signed in"],
                    instrument_id=m.instrument_id,
                    run_id=run.run_id,
                )
                continue
            used = sum(
                (
                    x.capital
                    for x in self.managed.values()
                    if x.mode == "LIVE" and x.status == "ACTIVE" and x.account_id == armed.account_id
                ),
                Decimal(0),
            )
            capital = min(m.capital, armed.capital_cap - used)
            if capital <= 0:
                continue
            try:
                live = self._start_live(m, capital, armed)
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
                [
                    *reasons,
                    f"within the {armed.account_id} cap of {armed.capital_cap:,} set by {armed.armed_by}",
                ],
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

    def _start_live(self, m: Managed, capital: Decimal, armed: ArmedAccount) -> Managed:
        lead = self._p.instruments.get(m.instruments[0])
        params = {**m.parameters, "capital": str(self.config.research_config().capital_for(lead, capital))}
        # The person who armed the account is the approver of record for every live autopilot deployment.
        deployment = self._launch(m.strategy, armed.account_id, params, m.instruments, armed.armed_by)
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
            account_id=armed.account_id,
            universe_id=m.universe_id,
            instruments=list(m.instruments),
            currency=m.currency,
            strategy=m.strategy,
        )
        m.live_deployment = deployment.deployment_id
        self._save(live)
        self._save(m)
        return live

    # ---- monitoring ---------------------------------------------------------------------------

    def monitor(self) -> None:
        """Mark P&L, roll expiring futures, close out drawdown breaches, and de-gross the whole book."""
        limit = Decimal(str(self.config.max_deployment_drawdown))
        with self._p.lock:
            for m in list(self.managed.values()):
                if m.status == "ACTIVE" and self._expiring(m):
                    self._roll(m)
                    continue
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
            for mode in ("PAPER", "LIVE"):
                self._check_book(mode)

    def _budget(self, mode: str) -> Decimal:
        if mode == "PAPER":
            return self.config.capital
        return sum((a.capital_cap for a in self.live.accounts.values()), Decimal(0))

    def protection(self, mode: str) -> dict[str, Any]:
        """Loss floor, cushion and the share of normal position sizes the book may still take."""
        budget = self._budget(mode)
        total = sum((m.pnl for m in self.managed.values() if m.mode == mode), Decimal(0))
        peak = max(self.peaks.get(mode, Decimal(0)), total)
        floor = budget * (1 - Decimal(str(self.config.loss_floor)))
        if peak > 0:  # lock in part of the best gain so far
            floor = max(floor, budget + peak * Decimal(str(self.config.lock_in_gains)))
        equity = budget + total
        room = budget * Decimal(str(self.config.loss_floor))
        exposure = max(Decimal(0), min(Decimal(1), (equity - floor) / room)) if room > 0 else Decimal(1)
        return {
            "budget": budget,
            "equity": equity,
            "floor": floor.quantize(Decimal("0.01")),
            "cushion": (equity - floor).quantize(Decimal("0.01")),
            "exposure": exposure.quantize(Decimal("0.01")),
            "floor_hit": bool(self.floor_hit.get(mode)),
        }

    def reset_protection(self, mode: str, actor: str) -> None:
        """Start protection afresh from the current results (after a floor stop, at the owner's request)."""
        total = sum((m.pnl for m in self.managed.values() if m.mode == mode), Decimal(0))
        for m in self.managed.values():
            if m.mode == mode and m.status != "ACTIVE":
                m.pnl = m.peak_pnl = Decimal(0)  # settled history no longer counts against the new floor
                self._save(m)
        self.floor_hit[mode] = False
        self.peaks[mode] = Decimal(0)
        self.halted_until.pop(mode, None)
        self._save_state()
        self._decide(
            "ARM" if mode == "LIVE" else "KEEP",
            f"Capital protection reset for the {mode.lower()} book",
            [f"cumulative result before the reset: {total:,.2f}; the floor is measured from here"],
            actor=actor,
        )

    def _check_book(self, mode: str) -> None:
        """Book-level stops: the loss floor (with locked-in gains) and the drawdown-from-peak limit."""
        book = [m for m in self.managed.values() if m.mode == mode]
        if not book:
            return
        total = sum((m.pnl for m in book), Decimal(0))
        budget = self._budget(mode)
        peak = max(self.peaks.get(mode, Decimal(0)), total)
        self.peaks[mode] = peak
        active = [m for m in book if m.status == "ACTIVE"]
        guard = self.protection(mode)
        if active and not self.floor_hit.get(mode) and guard["equity"] <= guard["floor"]:
            for m in active:
                self._close(m, "loss floor reached")
            self.floor_hit[mode] = True
            self._decide(
                "HALT",
                f"{mode.title()} book at its loss floor: everything moved to cash",
                [
                    f"equity {guard['equity']:,.2f} reached the floor {guard['floor']:,.2f} "
                    f"({self.config.loss_floor:.0%} of the budget, raised to keep "
                    f"{self.config.lock_in_gains:.0%} of gains)",
                    "no new positions until capital protection is reset",
                ],
            )
            self._save_state()
            return
        limit = Decimal(str(self.config.portfolio_drawdown_limit))
        if budget > 0 and active and (peak - total) / budget > limit:
            for m in active:
                self._close(m, "portfolio drawdown limit")
            self.halted_until[mode] = self._p.clock.now() + timedelta(days=self.config.halt_cooldown_days)
            self.peaks[mode] = total
            self._decide(
                "HALT",
                f"{mode.title()} book cut to cash: drawdown {(peak - total) / budget:.1%} of the budget",
                [
                    f"the limit is {limit:.0%}; {len(active)} deployments were closed",
                    f"new deployments resume after {self.halted_until[mode]:%Y-%m-%d} (UTC)",
                ],
            )
        self._save_state()

    def _halted(self, mode: str) -> bool:
        until = self.halted_until.get(mode)
        return until is not None and self._p.clock.now() < until

    def _expiring(self, m: Managed) -> bool:
        for instrument_id in m.instruments:
            instrument = self._p.instruments.get(instrument_id)
            if instrument.expiry and instrument.expiry <= self._p.clock.now() + timedelta(
                days=self.config.roll_days
            ):
                return True
        return False

    def _roll(self, m: Managed) -> None:
        """Move a futures deployment to the next contract before expiry, keeping its track record."""
        current = self._p.instruments.get(m.instrument_id)
        following = self._front(current.venue, current.underlying or "", after=current.expiry)
        if following is None:
            self._retire(m, [f"{current.instrument_id} expires soon and no later contract is loaded"])
            return
        approver = AUTOPILOT
        if m.mode == "LIVE":
            armed = self.live.accounts.get(m.account_id)
            if armed is None:
                self._retire(m, [f"{current.instrument_id} expires soon and live trading is no longer armed"])
                return
            approver = armed.armed_by
        self._close(m, f"rolling to {following.instrument_id}")
        try:
            deployment = self._launch(
                m.strategy, m.account_id, m.parameters, [following.instrument_id], approver
            )
        except PlatformError as exc:
            self._decide(
                "SKIP",
                f"Could not roll {m.label} to {following.instrument_id}",
                [exc.message],
                instrument_id=following.instrument_id,
            )
            return
        rolled = Managed(
            deployment.deployment_id,
            following.instrument_id,
            m.candidate,
            m.label,
            m.signal,
            m.mode,
            m.capital,
            m.parameters,
            m.created_at,
            expected=m.expected,
            model=m.model,
            source_deployment=m.source_deployment,
            account_id=m.account_id,
            universe_id=m.universe_id,
            currency=m.currency,
            strategy=m.strategy,
            live_deployment=m.live_deployment,
            ready_for_live_noted=m.ready_for_live_noted,
        )
        m.rolled_to = deployment.deployment_id
        self._save(m)
        self._save(rolled)
        for other in self.managed.values():  # keep paper <-> live links pointing at the new deployment
            if other.live_deployment == m.deployment_id:
                other.live_deployment = rolled.deployment_id
                self._save(other)
            if other.source_deployment == m.deployment_id:
                other.source_deployment = rolled.deployment_id
                self._save(other)
        self._decide(
            "ROLL",
            f"Rolled {m.label} from {current.instrument_id} to {following.instrument_id} ({m.mode.lower()})",
            [
                f"{current.instrument_id} expires {current.expiry:%Y-%m-%d}; the position was closed "
                "and the strategy "
                "continues on the next contract"
            ],
            instrument_id=following.instrument_id,
            deployment_id=rolled.deployment_id,
        )

    def fx_rates(self) -> dict[str, Decimal]:
        """INR per unit of each currency: live cross rates where the platform prices the pair.

        The dollar rate itself (and USDT) comes from the settings, since INR pairs are not quoted by
        forex brokers; every other currency is converted through the dollar at its live price.
        """
        rates = {**DEFAULT_FX, **self.config.fx_rates}
        usd = rates["USD"]
        for currency in list(rates):
            if currency in ("INR", "USD", "USDT", "USDC"):
                continue
            direct = self._p.market.reference_price(f"OANDA:{currency}_USD")
            inverse = self._p.market.reference_price(f"OANDA:USD_{currency}")
            if direct:
                rates[currency] = (usd * direct).quantize(Decimal("0.0001"))
            elif inverse:
                rates[currency] = (usd / inverse).quantize(Decimal("0.0001"))
        return rates

    def _mark(self, m: Managed) -> None:
        pnl = Decimal(0)
        for p in self._p.positions.positions(deployment_id=m.deployment_id, open_only=False):
            pnl += p.realized_pnl - p.fees_paid
            price = self._p.market.reference_price(p.instrument_id)
            if p.quantity and price is not None:
                pnl += p.unrealized_pnl(price)
        rate = self.fx_rates().get(m.currency, Decimal(1))
        pnl = (pnl * rate).quantize(Decimal("0.01"))  # into the budget currency
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
            "halted_until": {k: v.isoformat() for k, v in self.halted_until.items() if self._halted(k)},
            "protection": {mode: encode(self.protection(mode)) for mode in ("PAPER", "LIVE")},
            "progress": dict(self.progress),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": (n.isoformat() if (n := self.next_run_at()) else None),
            "paper_account": PAPER_AI_ACCOUNT,
            "analyst_available": self._analyst is not None,
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

    def _review_briefing(self, result: ResearchResult, run: Run) -> dict[str, Any]:
        keys = ("return", "sharpe", "max_drawdown", "trades", "positive_folds", "charges", "charges_share")

        def brief(e: Evaluation) -> dict[str, Any]:
            return {
                "instrument_id": e.instrument_id,
                "strategy": e.candidate.label,
                "validation": {k: e.validation.get(k) for k in keys},
                "holdout": {k: e.holdout.get(k) for k in ("return", "sharpe", "trades", "charges")},
                "fold_returns": e.fold_returns,
                "luck_adjusted_confidence": e.dsr,
            }

        rejected = sorted(
            (e for e in result.evaluations if not e.passed), key=lambda e: e.score, reverse=True
        )
        return {
            "data_source": run.data_source,
            "strategies_tested": result.trials,
            "budget": str(self.config.capital),
            "selected": [{**brief(s.evaluation), "capital": str(s.capital)} for s in result.selections],
            "best_rejected": [{**brief(e), "reasons": e.reasons} for e in rejected[:5]],
            "running": [
                {"instrument_id": m.instrument_id, "strategy": m.label, "mode": m.mode, "pnl": str(m.pnl)}
                for m in self.managed.values()
                if m.status == "ACTIVE"
            ],
            "protection": {k: str(v) for k, v in self.protection("PAPER").items()},
            "live_trading_armed_accounts": sorted(self.live.accounts),
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
