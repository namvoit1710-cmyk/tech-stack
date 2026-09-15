"""SQLite-backed ``ITimerRepository`` — SA-1371.

Durable pending-timer store for local dev, CI, and the first production
wiring. Mirrors the ``MemoryTimerRepository`` contract exactly, differing only
in durability (timers survive process restart when a file path is used) and in
serialisation of the record through a JSON ``metadata`` column.

Follows the same connection / locking pattern as
``SqliteEventLogRepository``: a single ``sqlite3.Connection`` with
``check_same_thread=False`` behind a ``threading.Lock`` in autocommit mode
(``isolation_level=None``). Sufficient for the single-process engine;
multi-replica deployments use the HANA backend.

Schema:
    timers
      timer_id     TEXT PRIMARY KEY       -- deterministic (run+node+kind)
      run_id       TEXT NOT NULL
      workflow_id  TEXT
      node_id      TEXT
      kind         TEXT                   -- "delay" | "sla" | "schedule"
      fire_at      TEXT                   -- ISO-8601 instant to fire at
      created_at   TEXT
      metadata     TEXT                   -- JSON-serialised dict
    idx_timers_fire_at ON timers (fire_at)   -- list_due scan
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from app.layer1_domain.entities.timer import Timer
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS timers (
    timer_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    workflow_id  TEXT,
    node_id      TEXT,
    kind         TEXT,
    fire_at      TEXT,
    created_at   TEXT,
    metadata     TEXT
)
"""

_CREATE_FIRE_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_timers_fire_at ON timers (fire_at)
"""

_UPSERT_SQL = """
INSERT INTO timers (
    timer_id, run_id, workflow_id, node_id, kind, fire_at, created_at, metadata
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(timer_id) DO UPDATE SET
    run_id      = excluded.run_id,
    workflow_id = excluded.workflow_id,
    node_id     = excluded.node_id,
    kind        = excluded.kind,
    fire_at     = excluded.fire_at,
    created_at  = excluded.created_at,
    metadata    = excluded.metadata
"""

_SELECT_DUE_SQL = """
SELECT timer_id, run_id, workflow_id, node_id, kind, fire_at, created_at, metadata
FROM timers
WHERE fire_at != '' AND fire_at <= ?
ORDER BY fire_at
LIMIT ?
"""

_SELECT_BY_RUN_SQL = """
SELECT timer_id, run_id, workflow_id, node_id, kind, fire_at, created_at, metadata
FROM timers
WHERE run_id = ?
"""

_SELECT_BY_ID_SQL = """
SELECT timer_id, run_id, workflow_id, node_id, kind, fire_at, created_at, metadata
FROM timers
WHERE timer_id = ?
"""

_DELETE_SQL = """
DELETE FROM timers WHERE timer_id = ?
"""


class SqliteTimerRepository(ITimerRepository):
    """Durable SQLite-backed pending-timer store.

    Pass ``":memory:"`` for an in-process ephemeral store or a filesystem path
    for a persistent store.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._connection = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit — one statement per call
        )
        self._lock = threading.Lock()
        self._initialise_schema()

    def _initialise_schema(self) -> None:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_CREATE_TABLE_SQL)
                cursor.execute(_CREATE_FIRE_AT_INDEX_SQL)
            finally:
                cursor.close()

    def save(self, timer: Timer) -> None:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(
                    _UPSERT_SQL,
                    (
                        timer.timer_id,
                        timer.run_id,
                        timer.workflow_id,
                        timer.node_id,
                        timer.kind,
                        timer.fire_at,
                        timer.created_at,
                        json.dumps(timer.metadata or {}, default=str),
                    ),
                )
            finally:
                cursor.close()

    def list_due(self, now_iso: str, limit: int = 100) -> list[Timer]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_DUE_SQL, (now_iso, int(limit)))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return [self._row_to_timer(row) for row in rows]

    def list_by_run(self, run_id: str) -> list[Timer]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_BY_RUN_SQL, (run_id,))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return [self._row_to_timer(row) for row in rows]

    def get(self, timer_id: str) -> Timer | None:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_BY_ID_SQL, (timer_id,))
                row = cursor.fetchone()
            finally:
                cursor.close()
        return self._row_to_timer(row) if row else None

    def delete(self, timer_id: str) -> bool:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_DELETE_SQL, (timer_id,))
                return cursor.rowcount == 1
            finally:
                cursor.close()

    @staticmethod
    def _row_to_timer(row: Any) -> Timer:
        metadata_raw = row[7]
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return Timer(
            timer_id=row[0],
            run_id=row[1],
            workflow_id=row[2] or "",
            node_id=row[3] or "",
            kind=row[4] or "delay",
            fire_at=row[5] or "",
            created_at=row[6] or "",
            metadata=metadata,
        )

    def close(self) -> None:
        """Close the underlying connection (test teardown / shutdown)."""
        with self._lock:
            self._connection.close()
