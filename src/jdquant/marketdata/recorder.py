"""Recording of order books, ticks and market events for replay and research.

Recorded data lives in its own SQLite file (`market.db` next to the platform database) so tick volume
never slows the trading journal. Books are stored compressed. Old rows are purged after the retention
period.

Market data belongs to the exchanges and is licensed through your brokers. Broker API access generally
permits personal, non-display use and forbids redistribution. Recording is therefore off until the owner
acknowledges that the data is for their own analysis only; nothing here exports or shares it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jdquant.core.clock import Clock
from jdquant.marketdata.book import BookSnapshot, Level, Tick

log = logging.getLogger(__name__)

MIGRATIONS = [
    """
    CREATE TABLE books (
        instrument TEXT NOT NULL,
        source TEXT NOT NULL,
        ts REAL NOT NULL,
        data BLOB NOT NULL
    );
    CREATE INDEX books_instrument_ts ON books(instrument, ts);
    CREATE TABLE ticks (
        instrument TEXT NOT NULL,
        source TEXT NOT NULL,
        ts REAL NOT NULL,
        price REAL NOT NULL,
        quantity REAL NOT NULL,
        side TEXT NOT NULL,
        volume REAL
    );
    CREATE INDEX ticks_instrument_ts ON ticks(instrument, ts);
    """,
    """
    CREATE TABLE market_events (
        event_id TEXT PRIMARY KEY,
        instrument TEXT NOT NULL,
        kind TEXT NOT NULL,
        ts REAL NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX market_events_instrument_ts ON market_events(instrument, ts);
    CREATE INDEX market_events_kind_ts ON market_events(kind, ts);
    CREATE INDEX market_events_ts ON market_events(ts);
    CREATE TABLE event_links (
        parent TEXT NOT NULL,
        child TEXT NOT NULL,
        relation TEXT NOT NULL,
        PRIMARY KEY (parent, child)
    );
    CREATE INDEX event_links_child ON event_links(child);
    """,
]


class MarketStore:
    """SQLite for bulk market data: WAL, normal sync (losing the last second on a crash is acceptable)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            applied = {r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")}
            for version, script in enumerate(MIGRATIONS, start=1):
                if version not in applied:
                    self._conn.executescript(
                        f"BEGIN;\n{script}\nINSERT INTO schema_migrations VALUES ({version});\nCOMMIT;"
                    )

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, rows)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def size_bytes(self) -> int:
        if self.path == ":memory:":
            row = self.query("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
            return int(row[0][0])
        total = 0
        for suffix in ("", "-wal"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def encode_book(book: BookSnapshot) -> bytes:
    data = {
        "b": [[lv.price, lv.quantity, lv.orders] for lv in book.bids],
        "a": [[lv.price, lv.quantity, lv.orders] for lv in book.asks],
        "x": book.exchange_ts.timestamp(),
        "lp": book.last_price,
        "lq": book.last_quantity,
        "v": book.volume,
        "oi": book.open_interest,
        "tb": book.total_buy_quantity,
        "ts": book.total_sell_quantity,
        "c": book.capacity,
    }
    return zlib.compress(json.dumps(data, separators=(",", ":")).encode(), 6)


def decode_book(instrument_id: str, source: str, received: float, blob: bytes) -> BookSnapshot:
    data = json.loads(zlib.decompress(blob))
    return BookSnapshot(
        instrument_id,
        source,
        datetime.fromtimestamp(data["x"], UTC),
        datetime.fromtimestamp(received, UTC),
        bids=tuple(Level(p, q, o) for p, q, o in data["b"]),
        asks=tuple(Level(p, q, o) for p, q, o in data["a"]),
        last_price=data.get("lp"),
        last_quantity=data.get("lq"),
        volume=data.get("v"),
        open_interest=data.get("oi"),
        total_buy_quantity=data.get("tb"),
        total_sell_quantity=data.get("ts"),
        capacity=data.get("c") or 5,
    )


class MarketRecorder:
    """Buffers books and ticks from the hub and writes them in batches."""

    def __init__(self, store: MarketStore, clock: Clock, *, flush_every: float = 1.0, batch: int = 2000):
        self.store = store
        self._clock = clock
        self.enabled = False
        self.acknowledged_by: str | None = None
        self.retention_days = 30
        self.instruments: set[str] | None = None  # None: every instrument the hub sees
        self._books: list[tuple] = []
        self._ticks: list[tuple] = []
        self._last: dict[tuple[str, str], tuple] = {}
        self._lock = threading.Lock()
        self._batch = batch
        self._flush_every = flush_every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.books_written = 0
        self.ticks_written = 0

    def settings(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "acknowledged_by": self.acknowledged_by,
            "retention_days": self.retention_days,
            "instruments": None if self.instruments is None else sorted(self.instruments),
            "books_written": self.books_written,
            "ticks_written": self.ticks_written,
            "size_bytes": self.store.size_bytes(),
        }

    def configure(
        self,
        *,
        enabled: bool | None = None,
        acknowledged_by: str | None = None,
        retention_days: int | None = None,
        instruments: list[str] | None = None,
    ) -> None:
        if acknowledged_by:
            self.acknowledged_by = acknowledged_by
        if enabled is not None:
            if enabled and not self.acknowledged_by:
                from jdquant.core.errors import PlatformError

                raise PlatformError(
                    "DATA_USE_NOT_ACKNOWLEDGED",
                    "confirm that recorded market data is for your own analysis before recording",
                )
            self.enabled = enabled
        if retention_days is not None:
            self.retention_days = max(1, min(int(retention_days), 3650))
        if instruments is not None:
            self.instruments = set(instruments) or None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="market-recorder", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.flush()

    def _loop(self) -> None:
        last_purge = datetime.min.replace(tzinfo=UTC)
        while not self._stop.wait(self._flush_every):
            try:
                self.flush()
                now = self._clock.now()
                if now - last_purge > timedelta(hours=1):
                    self.purge()
                    last_purge = now
            except Exception:
                log.exception("market recorder flush failed")

    def _wanted(self, instrument_id: str) -> bool:
        return self.enabled and (self.instruments is None or instrument_id in self.instruments)

    def on_book(self, book: BookSnapshot, previous: BookSnapshot | None) -> None:
        if not self._wanted(book.instrument_id):
            return
        signature = (book.bids, book.asks, book.last_price, book.volume)
        key = (book.instrument_id, book.source)
        with self._lock:
            if self._last.get(key) == signature:
                return  # unchanged book: nothing new to replay
            self._last[key] = signature
            self._books.append(
                (book.instrument_id, book.source, book.received_ts.timestamp(), encode_book(book))
            )
            full = len(self._books) >= self._batch
        if full:
            self.flush()

    def on_tick(self, tick: Tick) -> None:
        if not self._wanted(tick.instrument_id):
            return
        with self._lock:
            self._ticks.append(
                (
                    tick.instrument_id,
                    tick.source,
                    (tick.received_ts or tick.at).timestamp(),
                    tick.price,
                    tick.quantity,
                    tick.side,
                    tick.volume,
                )
            )

    def flush(self) -> None:
        with self._lock:
            books, self._books = self._books, []
            ticks, self._ticks = self._ticks, []
        self.store.executemany("INSERT INTO books VALUES (?, ?, ?, ?)", books)
        self.store.executemany("INSERT INTO ticks VALUES (?, ?, ?, ?, ?, ?, ?)", ticks)
        self.books_written += len(books)
        self.ticks_written += len(ticks)

    def purge(self) -> None:
        cutoff = (self._clock.now() - timedelta(days=self.retention_days)).timestamp()
        for table in ("books", "ticks", "market_events"):
            self.store.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))

    # ---- reading back -----------------------------------------------------------------------------------

    def coverage(self) -> list[dict[str, Any]]:
        """What has been recorded: per instrument, the first and last time and the row counts."""
        rows = self.store.query(
            "SELECT instrument, MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n "
            "FROM books GROUP BY instrument"
        )
        ticks = {
            r["instrument"]: r["n"]
            for r in self.store.query("SELECT instrument, COUNT(*) AS n FROM ticks GROUP BY instrument")
        }
        return [
            {
                "instrument_id": r["instrument"],
                "first": datetime.fromtimestamp(r["first"], UTC).isoformat(),
                "last": datetime.fromtimestamp(r["last"], UTC).isoformat(),
                "books": r["n"],
                "ticks": ticks.get(r["instrument"], 0),
            }
            for r in rows
        ]

    def books_between(
        self, instrument_id: str, start: datetime, end: datetime, source: str | None = None
    ) -> list[BookSnapshot]:
        sql = "SELECT source, ts, data FROM books WHERE instrument = ? AND ts >= ? AND ts <= ?"
        params: tuple = (instrument_id, start.timestamp(), end.timestamp())
        if source:
            sql += " AND source = ?"
            params += (source,)
        rows = self.store.query(sql + " ORDER BY ts", params)
        return [decode_book(instrument_id, r["source"], r["ts"], r["data"]) for r in rows]

    def ticks_between(self, instrument_id: str, start: datetime, end: datetime) -> list[Tick]:
        rows = self.store.query(
            "SELECT * FROM ticks WHERE instrument = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (instrument_id, start.timestamp(), end.timestamp()),
        )
        return [
            Tick(
                instrument_id,
                r["source"],
                datetime.fromtimestamp(r["ts"], UTC),
                r["price"],
                r["quantity"],
                r["side"],
                r["volume"],
            )
            for r in rows
        ]
