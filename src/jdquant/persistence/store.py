"""Embedded transactional store for single-node (T1) deployments (Chapter 83, DB-83011)."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: core trading state
    """
    CREATE TABLE documents (
        kind TEXT NOT NULL,
        id TEXT NOT NULL,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (kind, id)
    );
    CREATE TABLE fills (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        fill_id TEXT NOT NULL UNIQUE,
        order_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX fills_account ON fills(account_id);
    CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        partition_key TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX events_type ON events(event_type);
    CREATE TRIGGER fills_append_only_u BEFORE UPDATE ON fills BEGIN SELECT RAISE(ABORT, 'fills are append-only'); END;
    CREATE TRIGGER fills_append_only_d BEFORE DELETE ON fills BEGIN SELECT RAISE(ABORT, 'fills are append-only'); END;
    CREATE TRIGGER events_append_only_u BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    CREATE TRIGGER events_append_only_d BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    """,
    # 2: identity, secrets and audit
    """
    CREATE TABLE users (
        user_id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        data TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE api_keys (
        key_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE TABLE secrets (
        name TEXT PRIMARY KEY,
        ciphertext BLOB NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    CREATE TABLE audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_id TEXT NOT NULL UNIQUE,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        category TEXT NOT NULL,
        target TEXT,
        outcome TEXT NOT NULL,
        data TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL
    );
    CREATE INDEX audit_actor ON audit(actor);
    CREATE INDEX audit_action ON audit(action);
    CREATE TRIGGER audit_append_only_u BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
    CREATE TRIGGER audit_append_only_d BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
    """,
    # 3: AI inference records and LLM call log
    """
    CREATE TABLE inferences (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        inference_id TEXT NOT NULL UNIQUE,
        model TEXT NOT NULL,
        version INTEGER NOT NULL,
        stage TEXT NOT NULL,
        instrument_id TEXT,
        at TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX inferences_model ON inferences(model, version);
    CREATE TABLE llm_calls (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        user_id TEXT NOT NULL,
        template TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        outcome TEXT NOT NULL
    );
    CREATE INDEX llm_calls_user ON llm_calls(user_id, at);
    CREATE TRIGGER inferences_append_only_u BEFORE UPDATE ON inferences BEGIN SELECT RAISE(ABORT, 'inferences are append-only'); END;
    """,
]


class Store:
    """SQLite with WAL and full synchronous commits; safe for use from multiple threads."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            applied = {r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")}
            for version, script in enumerate(MIGRATIONS, start=1):
                if version in applied:
                    continue
                self._conn.executescript(
                    f"BEGIN;\n{script}\nINSERT INTO schema_migrations VALUES ({version});\nCOMMIT;"
                )

    @property
    def schema_version(self) -> int:
        return self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---- document helpers ---------------------------------------------------------------------

    def put(self, kind: str, doc_id: str, data: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO documents(kind, id, data) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, id) DO UPDATE SET data = excluded.data, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (kind, doc_id, json.dumps(data, separators=(",", ":"))),
        )

    def get(self, kind: str, doc_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT data FROM documents WHERE kind = ? AND id = ?", (kind, doc_id))
        return json.loads(rows[0]["data"]) if rows else None

    def all(self, kind: str) -> list[dict[str, Any]]:
        return [
            json.loads(r["data"]) for r in self.query("SELECT data FROM documents WHERE kind = ?", (kind,))
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
