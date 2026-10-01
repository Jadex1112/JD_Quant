"""Model Manager (Chapter 57) and Inference Pipeline (Chapter 59)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from jdquant.ai.features import FeatureSet
from jdquant.ai.training import LinearModel, psi
from jdquant.core.clock import Clock
from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.core.events import EventBus
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store


class Stage(StrEnum):
    NONE = "NONE"
    STAGING = "STAGING"
    SHADOW = "SHADOW"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"


PROMOTION_ORDER = [Stage.NONE, Stage.STAGING, Stage.SHADOW, Stage.PRODUCTION]


@dataclass
class ModelVersion:
    model: str
    version: int
    artifact: LinearModel
    checksum: str
    feature_set: FeatureSet
    report: dict[str, Any]
    created_by: str
    created_at: datetime
    instrument_id: str | None = None
    stage: Stage = Stage.NONE
    approvals: list[dict[str, str]] = field(default_factory=list)
    rollback_target: int | None = None
    model_card: str = ""


@dataclass
class Prediction:
    inference_id: str
    model: str
    version: int
    stage: Stage
    value: float
    features: dict[str, float]
    shadow: dict[str, float] = field(default_factory=dict)


class ModelRegistry:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        bus: EventBus | None = None,
        *,
        single_user: bool = True,
        min_test_auc: float = 0.5,
        drift_alert: float = 0.25,
    ):
        self._store = store
        self._clock = clock
        self._bus = bus
        self.single_user = single_user
        self.min_test_auc = min_test_auc
        self.drift_alert = drift_alert
        self._recent_inputs: dict[tuple[str, int], list[list[float]]] = {}

    # ---- registry ---------------------------------------------------------------------------

    def register(
        self,
        name: str,
        artifact: LinearModel,
        feature_set: FeatureSet,
        report: dict[str, Any],
        *,
        created_by: str,
        instrument_id: str | None = None,
    ) -> ModelVersion:
        versions = self.versions(name)
        mv = ModelVersion(
            model=name,
            version=(max(v.version for v in versions) + 1) if versions else 1,
            artifact=artifact,
            checksum=artifact.checksum,
            feature_set=feature_set,
            report=report,
            created_by=created_by,
            created_at=self._clock.now(),
            instrument_id=instrument_id,
            model_card=(
                f"{artifact.algorithm} on {len(feature_set.features)} features predicting the sign/size "
                "of the "
                f"{artifact.horizon}-bar forward return{f' of {instrument_id}' if instrument_id else ''}. "
                "Trained on historical data; performance can decay as market regimes change. "
                "Outputs are research signals, not investment advice."
            ),
        )
        self._save(mv)
        return mv

    def versions(self, name: str) -> list[ModelVersion]:
        return sorted(
            (decode(ModelVersion, d) for d in self._store.all(f"model_version:{name}")),
            key=lambda v: v.version,
        )

    def models(self) -> list[str]:
        rows = self._store.query("SELECT DISTINCT kind FROM documents WHERE kind LIKE 'model_version:%'")
        return sorted(r["kind"].split(":", 1)[1] for r in rows)

    def get(self, name: str, version: int) -> ModelVersion:
        doc = self._store.get(f"model_version:{name}", str(version))
        if doc is None:
            raise NotFoundError("MODEL_NOT_FOUND", f"unknown model {name} v{version}")
        mv = decode(ModelVersion, doc)
        if mv.artifact.checksum != mv.checksum:  # AI-57002
            raise PlatformError("MODEL_CHECKSUM_MISMATCH", f"{name} v{version} artifact failed verification")
        return mv

    def in_stage(self, name: str, stage: Stage) -> ModelVersion | None:
        return next((v for v in self.versions(name) if v.stage is stage), None)

    # ---- lifecycle (AI-57003, AI-57004, AI-57006) --------------------------------------------

    def promote(
        self, name: str, version: int, target: Stage, *, approver: str, reason: str = ""
    ) -> ModelVersion:
        mv = self.get(name, version)
        current_index = PROMOTION_ORDER.index(mv.stage) if mv.stage in PROMOTION_ORDER else None
        if (
            current_index is None
            or target not in PROMOTION_ORDER
            or PROMOTION_ORDER.index(target) != current_index + 1
        ):
            raise PlatformError(
                "INVALID_STATE_TRANSITION",
                f"cannot move {mv.stage.value} → {target.value}; "
                "stages advance NONE → STAGING → SHADOW → PRODUCTION",
            )
        if target is Stage.PRODUCTION:
            self._check_production_gate(mv, approver)
            current = self.in_stage(name, Stage.PRODUCTION)
            if current is not None:
                current.stage = Stage.ARCHIVED
                self._save(current)
                mv.rollback_target = current.version
        elif target is Stage.SHADOW:
            current = self.in_stage(name, Stage.SHADOW)
            if current is not None:
                current.stage = Stage.STAGING
                self._save(current)
        mv.stage = target
        mv.approvals.append(
            {
                "approver": approver,
                "stage": target.value,
                "at": self._clock.now().isoformat(),
                "reason": reason,
            }
        )
        self._save(mv)
        self._publish("model.stage.changed", {"model": name, "version": version, "stage": target.value})
        return mv

    def _check_production_gate(self, mv: ModelVersion, approver: str) -> None:
        test = mv.report.get("metrics", {}).get("test", {})
        problems = []
        if not mv.report:
            problems.append("an evaluation report is required")
        if "auc" in test and (test["auc"] is None or test["auc"] < self.min_test_auc):
            problems.append(f"test AUC {test.get('auc')} is below {self.min_test_auc}")
        if approver == mv.created_by and not self.single_user:
            problems.append("the model's creator cannot approve it for production (CON-203)")
        if problems:
            raise PlatformError("PROMOTION_BLOCKED", "; ".join(problems))

    def rollback(self, name: str, *, actor: str) -> ModelVersion:
        current = self.in_stage(name, Stage.PRODUCTION)
        if current is None or current.rollback_target is None:
            raise PlatformError("NO_ROLLBACK_TARGET", f"{name} has no production version to roll back to")
        target = self.get(name, current.rollback_target)
        current.stage = Stage.ARCHIVED
        target.stage = Stage.PRODUCTION
        target.approvals.append(
            {
                "approver": actor,
                "stage": "ROLLBACK",
                "at": self._clock.now().isoformat(),
                "reason": f"rollback from v{current.version}",
            }
        )
        self._save(current)
        self._save(target)
        self._publish("model.rolled_back", {"model": name, "from": current.version, "to": target.version})
        return target

    # ---- inference (Chapter 59) -------------------------------------------------------------

    def predict(
        self, name: str, window, *, instrument_id: str | None = None, record: bool = True
    ) -> Prediction:
        """Score the latest bar with the PRODUCTION version; SHADOW scores are recorded, never returned."""
        production = self.in_stage(name, Stage.PRODUCTION)
        if production is None:
            raise PlatformError("MODEL_NOT_DEPLOYED", f"{name} has no PRODUCTION version")
        production = self.get(name, production.version)
        vector = production.feature_set.vector(window)
        if vector is None:
            raise PlatformError("INPUT_INVALID", "not enough history to compute every feature")
        value = production.artifact.score(vector)
        shadow = {}
        shadow_version = self.in_stage(name, Stage.SHADOW)
        if shadow_version is not None:
            shadow_vec = shadow_version.feature_set.vector(window)
            if shadow_vec is not None:
                shadow[f"v{shadow_version.version}"] = shadow_version.artifact.score(shadow_vec)
        prediction = Prediction(
            str(uuid.uuid4()),
            name,
            production.version,
            Stage.PRODUCTION,
            value,
            dict(zip(production.feature_set.names, vector, strict=True)),
            shadow,
        )
        self._recent_inputs.setdefault((name, production.version), []).append(vector)
        del self._recent_inputs[(name, production.version)][:-500]
        if record:
            self._store.execute(
                "INSERT INTO inferences(inference_id, model, version, stage, instrument_id, at, data) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    prediction.inference_id,
                    name,
                    production.version,
                    "PRODUCTION",
                    instrument_id,
                    self._clock.now().isoformat(),
                    json.dumps(encode(prediction)),
                ),
            )
        return prediction

    def drift(self, name: str) -> dict[str, float | None]:
        """PSI per feature of recent live inputs against the training distribution (AI-57008)."""
        production = self.in_stage(name, Stage.PRODUCTION)
        if production is None:
            return {}
        recent = self._recent_inputs.get((name, production.version), [])
        edges = production.report.get("reference_distribution", {})
        result = {}
        for i, feature in enumerate(production.feature_set.names):
            result[feature] = psi(edges[str(i)], [row[i] for row in recent]) if str(i) in edges else None
        alerts = {k: v for k, v in result.items() if v is not None and v > self.drift_alert}
        if alerts:
            self._publish(
                "model.drift.detected", {"model": name, "version": production.version, "psi": alerts}
            )
        return result

    def inference_count(self, name: str) -> int:
        return self._store.query("SELECT COUNT(*) AS n FROM inferences WHERE model = ?", (name,))[0]["n"]

    def _save(self, mv: ModelVersion) -> None:
        self._store.put(f"model_version:{mv.model}", str(mv.version), encode(mv))

    def _publish(self, event: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(event, payload, producer="mme")
