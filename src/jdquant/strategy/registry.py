"""Strategy versions and the path from an idea to live money.

Every strategy (a template with parameters, or typed rules from the strategy lab) is registered with an
id (STR-001...) and versions (1.0, 1.1, ...). Each version moves through fixed stages, and each step has
a gate that code enforces:

    DEVELOPMENT -> BACKTEST -> VALIDATION -> PAPER -> APPROVED -> LIVE

| Step | Gate |
|---|---|
| BACKTEST | a backtest ran (broker history or clearly labelled synthetic data) |
| VALIDATION | walk-forward on real history: most periods and the last period profitable, enough trades |
| PAPER | deployed on a paper account with live prices |
| APPROVED | enough paper days and trades, then a person approves (step-up authentication) |
| LIVE | deployed on a live account by a person; the account's capital limits and risk checks apply |

Nothing an AI writes can skip a stage. Rolling back stops the live version and redeploys the previous
approved one. Every step is kept in the version's history.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from jdquant.core.errors import NotFoundError, PlatformError, ValidationError

KIND = "strategy_registry"
STAGES = ("DEVELOPMENT", "BACKTEST", "VALIDATION", "PAPER", "APPROVED", "LIVE", "RETIRED")


@dataclass
class PipelineSettings:
    validation_folds: int = 4
    min_validation_trades: int = 10
    min_paper_days: float = 5.0
    min_paper_trades: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyVersion:
    version: str
    parameters: dict[str, Any]
    notes: str
    created_by: str
    created_at: str
    stage: str = "DEVELOPMENT"
    backtest: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    paper_deployment_id: str | None = None
    paper_started_at: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    live_deployments: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StrategyDefinition:
    strategy_id: str
    name: str
    template: str
    instrument_id: str
    interval_seconds: int
    description: str
    created_by: str
    created_at: str
    versions: list[StrategyVersion] = field(default_factory=list)
    live_version: str | None = None

    def version(self, version: str) -> StrategyVersion:
        for v in self.versions:
            if v.version == version:
                return v
        raise NotFoundError("VERSION_NOT_FOUND", f"{self.strategy_id} has no version {version}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load(doc: dict[str, Any]) -> StrategyDefinition:
    versions = [StrategyVersion(**v) for v in doc.pop("versions", [])]
    return StrategyDefinition(**doc, versions=versions)


class StrategyRegistry:
    def __init__(self, store, platform, *, candles=None, journal=None, runner=None):
        self._store = store
        self._p = platform
        self._candles = candles  # (instrument, interval_seconds, bars) -> broker candles or None
        self._journal = journal
        self._runner = runner
        self._lock = threading.Lock()
        saved = store.get(KIND, "settings") or {}
        self.settings = PipelineSettings(
            **{k: v for k, v in saved.items() if k in PipelineSettings.__dataclass_fields__}
        )

    # ---- persistence ------------------------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        items = [d for d in self._store.all(KIND) if "strategy_id" in d]
        return sorted(items, key=lambda d: d["strategy_id"])

    def get(self, strategy_id: str) -> StrategyDefinition:
        doc = self._store.get(KIND, strategy_id)
        if doc is None:
            raise NotFoundError("STRATEGY_NOT_FOUND", f"unknown strategy {strategy_id}")
        return _load(doc)

    def _save(self, definition: StrategyDefinition) -> None:
        self._store.put(KIND, definition.strategy_id, definition.to_dict())

    def _log(self, version: StrategyVersion, stage: str, actor: str, note: str = "") -> None:
        version.stage = stage
        version.history.append(
            {"stage": stage, "at": self._p.clock.now().isoformat(), "by": actor, "note": note}
        )

    def configure(self, changes: dict[str, Any]) -> dict[str, Any]:
        values = {
            **self.settings.to_dict(),
            **{k: v for k, v in changes.items() if k in PipelineSettings.__dataclass_fields__},
        }
        self.settings = PipelineSettings(**values)
        self._store.put(KIND, "settings", self.settings.to_dict())
        return self.settings.to_dict()

    # ---- creating -------------------------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        template: str,
        instrument_id: str,
        interval_seconds: int,
        parameters: dict[str, Any],
        description: str,
        user: str,
    ) -> StrategyDefinition:
        from jdquant.strategy.base import validate_parameters
        from jdquant.strategy.templates import TEMPLATES

        cls = TEMPLATES.get(template)
        if cls is None or template in ("autopilot", "ai_trader", "rotation"):
            raise ValidationError(
                "TEMPLATE_UNKNOWN", [{"field": "template", "message": f"unknown template {template}"}]
            )
        self._p.instruments.get(instrument_id)
        validate_parameters(cls.parameters, parameters)
        with self._lock:
            number = 1 + max((int(d["strategy_id"][4:]) for d in self.list()), default=0)
            now = self._p.clock.now().isoformat()
            definition = StrategyDefinition(
                f"STR-{number:03d}",
                name.strip() or template,
                template,
                instrument_id,
                interval_seconds,
                description,
                user,
                now,
            )
            version = StrategyVersion("1.0", dict(parameters), "first version", user, now)
            self._log(version, "DEVELOPMENT", user, "created")
            definition.versions.append(version)
            self._save(definition)
        return definition

    def new_version(
        self, strategy_id: str, parameters: dict[str, Any], notes: str, user: str
    ) -> StrategyVersion:
        from jdquant.strategy.base import validate_parameters
        from jdquant.strategy.templates import TEMPLATES

        definition = self.get(strategy_id)
        validate_parameters(TEMPLATES[definition.template].parameters, parameters)
        major, minor = definition.versions[-1].version.split(".")
        version = StrategyVersion(
            f"{major}.{int(minor) + 1}", dict(parameters), notes or "", user, self._p.clock.now().isoformat()
        )
        self._log(version, "DEVELOPMENT", user, notes or "new version")
        definition.versions.append(version)
        self._save(definition)
        return version

    def from_lab(self, lab, run_id: str, *, name: str, user: str) -> StrategyDefinition:
        """Register rules typed in the strategy lab as a strategy, so they go through the same stages."""
        run = lab.get(run_id)
        parameters = {
            "spec": json.dumps(run.spec),
            "capital": run.capital_quote or "100000",
            "leverage": run.leverage or "1",
            "allow_short": True,
            "trade_after": "",
        }
        return self.create(
            name=name or f"Lab {run_id}",
            template="rules",
            instrument_id=run.instrument_id,
            interval_seconds=run.interval_seconds,
            parameters=parameters,
            description=run.text or "",
            user=user,
        )

    # ---- the pipeline ---------------------------------------------------------------------------------

    def _history(self, definition: StrategyDefinition, bars: int):
        instrument = self._p.instruments.get(definition.instrument_id)
        candles = self._candles(instrument, definition.interval_seconds, bars) if self._candles else None
        return instrument, candles

    def backtest(
        self, strategy_id: str, version: str, user: str, *, bars: int = 2000, synthetic_ok: bool = True
    ):
        from jdquant.backtest.engine import BacktestConfig, run_backtest
        from jdquant.marketdata.synthetic import random_walk_candles
        from jdquant.markets.india import fees_for

        definition = self.get(strategy_id)
        v = definition.version(version)
        instrument, candles = self._history(definition, bars)
        source = "broker history"
        if not candles:
            if not synthetic_ok:
                raise PlatformError("HISTORY_UNAVAILABLE", "connect the instrument's broker for its history")
            ref = self._p.market.reference_price(instrument.instrument_id) or Decimal(100)
            start = self._p.clock.now() - timedelta(seconds=definition.interval_seconds * bars)
            candles = random_walk_candles(
                instrument,
                start,
                bars,
                interval_seconds=definition.interval_seconds,
                start_price=Decimal(ref),
            )
            source = "synthetic (not evidence)"
        result = run_backtest(
            BacktestConfig(
                strategy=definition.template,
                instruments=[instrument],
                candles={instrument.instrument_id: candles},
                parameters=v.parameters,
                base_currency=instrument.quote_asset,
                fees=fees_for(instrument),
            )
        )
        v.backtest = {
            "at": self._p.clock.now().isoformat(),
            "data": source,
            "bars": len(candles),
            "metrics": {k: (float(x) if isinstance(x, Decimal) else x) for k, x in result.metrics.items()},
            "trades": len(result.trades),
            "final_equity": str(result.final_equity),
        }
        if v.stage == "DEVELOPMENT":
            self._log(v, "BACKTEST", user, f"backtest on {source}")
        self._save(definition)
        return v.backtest

    def validate(self, strategy_id: str, version: str, user: str, *, bars: int = 3000) -> dict[str, Any]:
        """Walk-forward on real history: each period is traded out of sample after a warm-up."""
        from jdquant.backtest.engine import BacktestConfig, run_backtest
        from jdquant.markets.india import fees_for

        definition = self.get(strategy_id)
        v = definition.version(version)
        if v.stage not in ("BACKTEST", "VALIDATION"):
            raise PlatformError("STAGE_ORDER", f"version {version} is {v.stage}; backtest it first")
        instrument, candles = self._history(definition, bars)
        if not candles or len(candles) < 400:
            raise PlatformError(
                "HISTORY_UNAVAILABLE", "validation needs at least 400 bars of real broker history"
            )
        folds = self.settings.validation_folds
        warmup = 200
        size = (len(candles) - warmup) // folds
        results = []
        for k in range(folds):
            start = warmup + k * size
            window = candles[start - warmup : start + size]
            r = run_backtest(
                BacktestConfig(
                    strategy=definition.template,
                    instruments=[instrument],
                    candles={instrument.instrument_id: window},
                    parameters=v.parameters,
                    base_currency=instrument.quote_asset,
                    fees=fees_for(instrument),
                )
            )
            curve = [float(e) for t, e in r.equity_curve if t >= window[warmup - 1].close_ts]
            ret = (curve[-1] / curve[0] - 1) if len(curve) > 1 and curve[0] else 0.0
            results.append(
                {
                    "period": k + 1,
                    "from": window[warmup].open_ts.isoformat(),
                    "to": window[-1].close_ts.isoformat(),
                    "return": ret,
                    "trades": len(r.trades),
                }
            )
        trades = sum(r["trades"] for r in results)
        profitable = sum(1 for r in results if r["return"] > 0)
        checks = [
            {
                "name": "Profitable in most periods",
                "ok": profitable > folds / 2,
                "detail": f"{profitable} of {folds}",
            },
            {
                "name": "Profitable in the last period",
                "ok": results[-1]["return"] > 0,
                "detail": f"{results[-1]['return']:.2%}",
            },
            {
                "name": "Enough trades",
                "ok": trades >= self.settings.min_validation_trades,
                "detail": f"{trades} (need {self.settings.min_validation_trades})",
            },
        ]
        passed = all(c["ok"] for c in checks)
        v.validation = {
            "at": self._p.clock.now().isoformat(),
            "periods": results,
            "checks": checks,
            "passed": passed,
        }
        self._log(
            v,
            "VALIDATION" if passed else "BACKTEST",
            user,
            "walk-forward validation passed" if passed else "walk-forward validation failed",
        )
        self._save(definition)
        return v.validation

    def start_paper(self, strategy_id: str, version: str, account_id: str, user: str) -> dict[str, Any]:
        from jdquant.trading.engine import AccountMode

        definition = self.get(strategy_id)
        v = definition.version(version)
        if v.stage != "VALIDATION":
            raise PlatformError(
                "STAGE_ORDER", f"version {version} is {v.stage}; it must pass validation first"
            )
        account = self._p.trading.get_account(account_id)
        if account.mode is not AccountMode.PAPER:
            raise PlatformError("PAPER_ONLY", "paper trading runs on a paper account")
        for other in definition.versions:  # one paper run per strategy: the newest version replaces it
            if other is not v and other.paper_deployment_id:
                self._stop(other.paper_deployment_id)
        deployment = self._deploy(definition, v, account_id, user)
        v.paper_deployment_id = deployment.deployment_id
        v.paper_started_at = self._p.clock.now().isoformat()
        self._log(v, "PAPER", user, f"paper deployment {deployment.deployment_id}")
        self._save(definition)
        return {"deployment_id": deployment.deployment_id}

    def paper_record(self, v: StrategyVersion) -> dict[str, Any]:
        if not v.paper_deployment_id or self._journal is None:
            return {"days": 0.0, "trades": 0, "net_pnl": 0.0}
        trades = self._journal.trades(deployment_id=v.paper_deployment_id, limit=10_000)
        started = datetime.fromisoformat(v.paper_started_at) if v.paper_started_at else self._p.clock.now()
        days = (self._p.clock.now() - started).total_seconds() / 86400
        pnls = [float(t["net_pnl"]) for t in trades]
        return {
            "days": round(days, 2),
            "trades": len(trades),
            "net_pnl": sum(pnls),
            "win_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
        }

    def approve(self, strategy_id: str, version: str, user: str, note: str = "") -> StrategyVersion:
        definition = self.get(strategy_id)
        v = definition.version(version)
        if v.stage != "PAPER":
            raise PlatformError(
                "STAGE_ORDER", f"version {version} is {v.stage}; it must be paper traded first"
            )
        record = self.paper_record(v)
        s = self.settings
        if record["days"] < s.min_paper_days or record["trades"] < s.min_paper_trades:
            raise PlatformError(
                "PAPER_RECORD_TOO_SHORT",
                f"paper record {record['days']:.1f} days and {record['trades']} trades; approval needs "
                f"{s.min_paper_days:g} days and {s.min_paper_trades} trades",
            )
        if v.created_by == user and not self._p.trading.single_user:
            raise PlatformError("FOUR_EYES", "a different person must approve a strategy they did not write")
        v.approved_by, v.approved_at = user, self._p.clock.now().isoformat()
        self._log(v, "APPROVED", user, note or "approved for live trading")
        self._save(definition)
        return v

    def go_live(self, strategy_id: str, version: str, account_id: str, user: str) -> dict[str, Any]:
        from jdquant.trading.engine import AccountMode

        definition = self.get(strategy_id)
        v = definition.version(version)
        if v.stage not in ("APPROVED", "LIVE"):
            raise PlatformError("STAGE_ORDER", f"version {version} is {v.stage}; it must be approved first")
        if self._p.trading.get_account(account_id).mode is not AccountMode.LIVE:
            raise PlatformError("LIVE_ACCOUNT_REQUIRED", "choose a live broker account")
        if definition.live_version and definition.live_version != version:
            self._stop_live(
                definition, definition.version(definition.live_version), user, f"replaced by {version}"
            )
        deployment = self._deploy(definition, v, account_id, user)
        v.live_deployments.append(deployment.deployment_id)
        definition.live_version = version
        self._log(v, "LIVE", user, f"live on {account_id} as {deployment.deployment_id}")
        self._save(definition)
        return {"deployment_id": deployment.deployment_id}

    def rollback(self, strategy_id: str, user: str) -> dict[str, Any]:
        """Stop the live version and put the most recent earlier approved version live in its place."""
        definition = self.get(strategy_id)
        if not definition.live_version:
            raise PlatformError("NOT_LIVE", "no version of this strategy is live")
        current = definition.version(definition.live_version)
        earlier = [v for v in definition.versions if v.version != current.version and v.approved_at]
        if not earlier:
            raise PlatformError("NOTHING_TO_ROLL_BACK_TO", "no earlier approved version")
        target = sorted(earlier, key=lambda v: [int(x) for x in v.version.split(".")])[-1]
        accounts = [
            self._p.trading.deployments[d].account_id
            for d in current.live_deployments
            if d in self._p.trading.deployments
        ]
        self._stop_live(definition, current, user, f"rolled back to {target.version}")
        definition.live_version = None
        self._save(definition)
        started = [
            self.go_live(strategy_id, target.version, a, user)["deployment_id"]
            for a in dict.fromkeys(accounts)
        ]
        return {"rolled_back_from": current.version, "to": target.version, "deployments": started}

    def retire(self, strategy_id: str, version: str, user: str) -> StrategyVersion:
        definition = self.get(strategy_id)
        v = definition.version(version)
        if definition.live_version == version:
            self._stop_live(definition, v, user, "retired")
            definition.live_version = None
        if v.paper_deployment_id:
            self._stop(v.paper_deployment_id)
        self._log(v, "RETIRED", user, "retired")
        self._save(definition)
        return v

    # ---- deployments ------------------------------------------------------------------------------------

    def _deploy(self, definition: StrategyDefinition, v: StrategyVersion, account_id: str, user: str):
        trading = self._p.trading
        with self._p.lock:
            deployment = trading.create_deployment(
                strategy_name=definition.template,
                strategy_version=f"{definition.strategy_id}@{v.version}",
                account_id=account_id,
                parameters=dict(v.parameters),
                instruments=[definition.instrument_id],
                created_by=v.created_by,
                bar_interval_seconds=definition.interval_seconds,
            )
            # Live money was approved by the person who signed off the version (four eyes when there
            # are several users); a paper deployment is approved by whoever starts it.
            approver = (
                v.approved_by
                if trading.get_account(account_id).mode.value == "LIVE" and v.approved_by
                else user
            )
            trading.approve(deployment.deployment_id, approver)
            trading.start(deployment.deployment_id)
            if self._runner is not None:
                self._runner.sync(deployment)
        return deployment

    def _stop(self, deployment_id: str) -> None:
        from jdquant.trading.engine import DeploymentState

        deployment = self._p.trading.deployments.get(deployment_id)
        if deployment is not None and deployment.state in (DeploymentState.RUNNING, DeploymentState.PAUSED):
            with self._p.lock:
                self._p.trading.stop(deployment_id)
                if self._runner is not None:
                    self._runner.sync(deployment)

    def _stop_live(self, definition, v: StrategyVersion, user: str, note: str) -> None:
        for deployment_id in v.live_deployments:
            self._stop(deployment_id)
        v.history.append(
            {
                "stage": v.stage,
                "at": self._p.clock.now().isoformat(),
                "by": user,
                "note": f"live deployments stopped: {note}",
            }
        )
        if v.stage == "LIVE":
            v.stage = "APPROVED"

    def view(self, strategy_id: str) -> dict[str, Any]:
        definition = self.get(strategy_id)
        out = definition.to_dict()
        for v, d in zip(definition.versions, out["versions"], strict=True):
            d["paper_record"] = self.paper_record(v)
            d["deployments"] = {
                dep: self._p.trading.deployments[dep].state.value
                for dep in [v.paper_deployment_id, *v.live_deployments]
                if dep and dep in self._p.trading.deployments
            }
        out["stages"] = list(STAGES)
        out["settings"] = self.settings.to_dict()
        return out
