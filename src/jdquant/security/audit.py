"""Append-only, hash-chained audit trail (Chapter 38, CON-085)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from jdquant.core.clock import Clock
from jdquant.persistence.codec import encode
from jdquant.persistence.store import Store

GENESIS = "0" * 64
SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "totp", "code", "credential")


def _mask(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: "***" if any(s in k.lower() for s in SENSITIVE_KEYS) else _mask(v) for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask(v) for v in data]
    return data


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    audit_id: str
    at: str
    actor: str
    action: str
    category: str
    target: str | None
    outcome: str
    data: dict[str, Any]
    previous_hash: str
    record_hash: str


def _hash(previous: str, content: dict[str, Any]) -> str:
    body = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((previous + body).encode()).hexdigest()


class AuditLog:
    def __init__(self, store: Store, clock: Clock):
        self._store = store
        self._clock = clock

    def record(
        self,
        *,
        actor: str,
        action: str,
        category: str,
        target: str | None = None,
        outcome: str = "SUCCESS",
        reason: str | None = None,
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Write synchronously; if this raises, the caller's action must fail (FR-38001)."""
        payload = _mask(encode({"reason": reason, "details": data or {}, "context": context or {}}))
        audit_id = str(uuid.uuid4())
        at = self._clock.now().isoformat()
        with self._store.transaction() as conn:
            row = conn.execute("SELECT record_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            previous = row[0] if row else GENESIS
            content = {
                "audit_id": audit_id,
                "at": at,
                "actor": actor,
                "action": action,
                "category": category,
                "target": target,
                "outcome": outcome,
                "data": payload,
            }
            conn.execute(
                "INSERT INTO audit(audit_id, at, actor, action, category, target, outcome, data, "
                "previous_hash, record_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    audit_id,
                    at,
                    actor,
                    action,
                    category,
                    target,
                    outcome,
                    json.dumps(payload, sort_keys=True),
                    previous,
                    _hash(previous, content),
                ),
            )
        return audit_id

    def search(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        category: str | None = None,
        limit: int = 200,
    ) -> list[AuditRecord]:
        clauses, params = [], []
        for column, value in (("actor", actor), ("action", action), ("category", category)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._store.query(f"SELECT * FROM audit {where} ORDER BY seq DESC LIMIT ?", (*params, limit))
        return [self._row(r) for r in rows]

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the chain; returns (ok, first broken sequence number)."""
        previous = GENESIS
        for r in self._store.query("SELECT * FROM audit ORDER BY seq"):
            rec = self._row(r)
            content = {
                "audit_id": rec.audit_id,
                "at": rec.at,
                "actor": rec.actor,
                "action": rec.action,
                "category": rec.category,
                "target": rec.target,
                "outcome": rec.outcome,
                "data": rec.data,
            }
            if rec.previous_hash != previous or _hash(previous, content) != rec.record_hash:
                return False, rec.seq
            previous = rec.record_hash
        return True, None

    @staticmethod
    def _row(r) -> AuditRecord:
        return AuditRecord(
            r["seq"],
            r["audit_id"],
            r["at"],
            r["actor"],
            r["action"],
            r["category"],
            r["target"],
            r["outcome"],
            json.loads(r["data"]),
            r["previous_hash"],
            r["record_hash"],
        )
