"""SQLite-backed ``IEventLogRepository`` — Phase 4b.6.

Durable event log for local dev, CI, and the first production
projector-backed wiring. Mirrors the ``MemoryEventLogRepository``
contract exactly (verified by the conformance test suite in
``tests/unit/test_sqlite_event_log_repo.py``), differing only in:

- Durability: events survive process restart when a file path is used.
- Dedupe enforcement: the database's unique index on
  ``(run_id, event_id)`` catches duplicate appends at the storage
  layer, not in Python-level ``set`` state.
- Concurrency: all reads/writes are serialised through a single
  ``sqlite3.Connection`` with ``check_same_thread=False`` behind a
  ``threading.Lock``. This matches the pattern used by the memory
  repositories in this codebase and is sufficient for the
  single-process engine. Multi-process / multi-replica deployments
  should use HANA (when that backend lands) or Kafka's native event
  log partitioning.

Why sqlite3 instead of aiosqlite:
    The ``IEventLogRepository`` protocol is synchronous. Making it
    async would cascade through ``EventLogAppendingPublisher`` and
    the engine's entire publish path. sqlite3 operations against a
    local file are sub-millisecond for typical payload sizes, so the
    sync API is not a meaningful latency source on the publish path.
    The shadow-log write-ahead model further decouples publish from
    log-append: append failures are caught and do not block publish.

Schema:
    event_log
      run_id       TEXT NOT NULL          -- log partition key
      sequence     INTEGER NOT NULL       -- monotonic per run
      event_id     TEXT NOT NULL          -- dedupe key
      topic        TEXT                   -- original publish topic
      event_type   TEXT                   -- dataclass class name
      timestamp    TEXT                   -- ISO8601
      payload      TEXT                   -- JSON-serialised dict
      source       TEXT                   -- original emitter
      causation_id TEXT                   -- upstream event id (Phase 4b.2+)
      PRIMARY KEY (run_id, sequence)
      UNIQUE (run_id, event_id)           -- dedupe index
"""

from __future__ import annotations

import json
import sqlite3
import threading

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
    IEventLogRepository,
)
from typing import Any


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS event_log (
    run_id       TEXT NOT NULL,
    sequence     INTEGER NOT NULL,
    event_id     TEXT NOT NULL,
    topic        TEXT,
    event_type   TEXT,
    timestamp    TEXT,
    payload      TEXT,
    source       TEXT,
    causation_id TEXT,
    PRIMARY KEY (run_id, sequence)
)
"""

_CREATE_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_log_event_id
    ON event_log (run_id, event_id)
"""

_NEXT_SEQUENCE_SQL = """
SELECT COALESCE(MAX(sequence), 0) + 1
FROM event_log
WHERE run_id = ?
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO event_log (
    run_id, sequence, event_id, topic, event_type,
    timestamp, payload, source, causation_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_BY_RUN_SQL = """
SELECT run_id, sequence, event_id, topic, event_type,
       timestamp, payload, source, causation_id
FROM event_log
WHERE run_id = ?
ORDER BY sequence
"""

_SELECT_BY_RUN_SINCE_SQL = """
SELECT run_id, sequence, event_id, topic, event_type,
       timestamp, payload, source, causation_id
FROM event_log
WHERE run_id = ? AND sequence > ?
ORDER BY sequence
"""

_COUNT_BY_RUN_SQL = """
SELECT COUNT(*) FROM event_log WHERE run_id = ?
"""

# Position i holds sequence i+1 (contiguous per run), so a page is a
# (run_id, sequence) range seek — no OFFSET row-scan on large logs.
_SELECT_BY_RUN_PAGE_SQL = """
SELECT run_id, sequence, event_id, topic, event_type,
       timestamp, payload, source, causation_id
FROM event_log
WHERE run_id = ? AND sequence > ? AND sequence <= ?
ORDER BY sequence
"""

_DELETE_BY_RUN_SQL = """
DELETE FROM event_log WHERE run_id = ?
"""


class SqliteEventLogRepository(IEventLogRepository):
    """Durable SQLite-backed event log.

    Pass ``":memory:"`` for an in-process ephemeral store (useful for
    tests) or a filesystem path for a persistent log.
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
                cursor.execute(_CREATE_UNIQUE_INDEX_SQL)
            finally:
                cursor.close()

    # ─────────────────────────── append ────────────────────────────

    def append(self, envelope: EventLogEnvelope) -> bool:
        """Persist ``envelope`` under its ``run_id``.

        Returns ``True`` if inserted, ``False`` if an envelope with the
        same ``(run_id, event_id)`` already exists (dedupe via the
        unique index and ``INSERT OR IGNORE``'s ``rowcount == 0`` signal).

        Empty ``run_id`` is routed to the ``"__unscoped__"`` bucket to
        match the memory backend's contract. Callers SHOULD provide an
        explicit id.
        """
        run_id = envelope.run_id or "__unscoped__"
        payload_json = json.dumps(envelope.payload or {}, default=str)

        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_NEXT_SEQUENCE_SQL, (run_id,))
                next_sequence = int(cursor.fetchone()[0])

                cursor.execute(
                    _INSERT_SQL,
                    (
                        run_id,
                        next_sequence,
                        envelope.event_id,
                        envelope.topic,
                        envelope.event_type,
                        envelope.timestamp,
                        payload_json,
                        envelope.source,
                        envelope.causation_id,
                    ),
                )
                # INSERT OR IGNORE returns rowcount=0 when the unique
                # (run_id, event_id) index fires — that is the dedupe
                # signal we mirror from MemoryEventLogRepository.
                return cursor.rowcount == 1
            finally:
                cursor.close()

    # ────────────────────────── list_by_run ───────────────────────

    def list_by_run(self, run_id: str) -> list[EventLogEnvelope]:
        """Return every envelope logged for ``run_id`` in append order.

        Empty ``run_id`` resolves to the ``"__unscoped__"`` bucket.
        """
        key = run_id or "__unscoped__"
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_BY_RUN_SQL, (key,))
                rows = cursor.fetchall()
            finally:
                cursor.close()

        return [self._row_to_envelope(row) for row in rows]

    def list_by_run_since(
        self, run_id: str, min_sequence: int,
    ) -> list[EventLogEnvelope]:
        """Phase 4c.2: return envelopes with ``sequence > min_sequence``.

        Uses ``WHERE sequence > ?`` against the ``(run_id, sequence)``
        primary-key index, so the query is an O(log n) index seek +
        O(Δ) row fetch. The hot path — "cache is warm, no new events" —
        returns an empty list with one index lookup.
        """
        key = run_id or "__unscoped__"
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_BY_RUN_SINCE_SQL, (key, min_sequence))
                rows = cursor.fetchall()
            finally:
                cursor.close()

        return [self._row_to_envelope(row) for row in rows]

    def count_by_run(self, run_id: str) -> int:
        """Return the number of envelopes logged for ``run_id``."""
        key = run_id or "__unscoped__"
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_COUNT_BY_RUN_SQL, (key,))
                return int(cursor.fetchone()[0])
            finally:
                cursor.close()

    def list_by_run_page(
        self, run_id: str, offset: int, limit: int,
    ) -> list[EventLogEnvelope]:
        """Return up to ``limit`` envelopes from 0-based ``offset`` in append
        order, resolved as a ``(run_id, sequence)`` range seek."""
        key = run_id or "__unscoped__"
        offset = max(0, offset)
        limit = max(0, limit)
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_SELECT_BY_RUN_PAGE_SQL, (key, offset, offset + limit))
                rows = cursor.fetchall()
            finally:
                cursor.close()

        return [self._row_to_envelope(row) for row in rows]

    @staticmethod
    def _row_to_envelope(row: Any) -> EventLogEnvelope:
        return EventLogEnvelope(
            run_id="" if row[0] == "__unscoped__" else row[0],
            sequence=row[1],
            event_id=row[2] or "",
            topic=row[3] or "",
            event_type=row[4] or "",
            timestamp=row[5] or "",
            payload=json.loads(row[6]) if row[6] else {},
            source=row[7] or "",
            causation_id=row[8] or "",
        )

    # ────────────────────────────── clear ─────────────────────────

    def clear(self, run_id: str) -> int:
        """Drop every envelope for ``run_id``. Returns rows removed.

        Empty ``run_id`` clears the unscoped bucket. Unlike the memory
        repo, clearing does NOT reset the sequence counter explicitly —
        but because ``_NEXT_SEQUENCE_SQL`` uses
        ``MAX(sequence) + 1`` scoped to the run, a freshly cleared run
        starts at 1 on the next append (identical observable behavior).
        """
        key = run_id or "__unscoped__"
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(_DELETE_BY_RUN_SQL, (key,))
                return cursor.rowcount
            finally:
                cursor.close()

    # ───────────────────────── lifecycle ──────────────────────────

    def close(self) -> None:
        """Close the underlying connection.

        Tests that create a repository on a file path SHOULD call
        ``close()`` in teardown so the file is properly flushed and
        released. Production wiring via ``bootstrap.py`` will need to
        register this on application shutdown once the backend is
        flipped on.
        """
        with self._lock:
            self._connection.close()
