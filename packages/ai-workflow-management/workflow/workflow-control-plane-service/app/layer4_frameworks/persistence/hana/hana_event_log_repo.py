"""SAP HANA implementation of ``IEventLogRepository`` — Phase 4b.8.

Durable event log backing for SAP BTP production deployments. Mirrors
the semantics of ``MemoryEventLogRepository`` and
``SqliteEventLogRepository`` exactly — same append/list/dedupe/clear
contract — but persists to the ``AIW_EVENT_LOG`` table in HANA.

Architecture notes
==================

Follows the pattern established by the other HANA repositories in
``app/layer4_frameworks/persistence/hana/`` (run, task, workflow,
node_definition):

- Takes an ``IDatabaseConnection`` instance at construction time. In
  production this is a ``HanaConnectionManager``; tests inject a
  lightweight stub.
- Uses ``:p0``, ``:p1``, … named placeholders. The connection manager
  translates these to positional ``?`` before sending to HANA.
- Stores the payload dict as JSON in an ``NCLOB`` column. The table
  schema lives in ``db/migrations/025_create_aiw_event_log.sql``.
- Table name is unquoted ``AIW_EVENT_LOG``. HANA case-folds unquoted
  identifiers to uppercase, matching the quoted name used in the
  migration.

Dedupe via unique-constraint exception
--------------------------------------

Unlike SQLite (``INSERT OR IGNORE``), HANA has no silent "ignore"
mode. The repository instead attempts an ``INSERT`` and catches the
unique-constraint violation raised by the
``IDX_AIW_EVENT_LOG_EVENT_ID`` index. HANA error code **301** is the
standard unique-key duplicate code and is preserved on the exception
object's ``errorcode`` attribute by ``hdbcli``. ``append`` maps error
code 301 to ``False`` (dedupe signal) and re-raises any other error.

Sequence assignment
-------------------

Per-run monotonic sequence is computed with
``SELECT COALESCE(MAX(SEQUENCE), 0) + 1 FROM AIW_EVENT_LOG
WHERE RUN_ID = :p0``. For a single-process engine this is safe because
the connection manager serialises reads/writes through its pool. In a
multi-replica deployment, bus-level partitioning by ``run_id`` (Kafka
partitions, Event Mesh consumer groups) prevents two replicas from
writing the same run concurrently — which is the same invariant
Phase 4.2 requires.

Clear semantics
---------------

``clear(run_id)`` issues a ``DELETE`` and returns the affected row
count. As with SQLite, the next append for that run starts at
sequence 1 because ``COALESCE(MAX(...), 0) + 1`` rises from zero after
the cleared rows are gone. This matches the memory and SQLite backends
observably without needing an explicit sequence counter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
    IEventLogRepository,
)

logger = logging.getLogger(__name__)


# HANA error code for unique-constraint violation.
# Raised by the IDX_AIW_EVENT_LOG_EVENT_ID unique index when the same
# (RUN_ID, EVENT_ID) pair is inserted twice. The dedupe contract maps
# this to a False return from ``append``.
_HANA_UNIQUE_VIOLATION = 301


_TABLE = "AIW_EVENT_LOG"

_NEXT_SEQUENCE_SQL = (
    f"SELECT COALESCE(MAX(SEQUENCE), 0) + 1 AS NEXT_SEQ "
    f"FROM {_TABLE} WHERE RUN_ID = :p0"
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ("
    f"RUN_ID, SEQUENCE, EVENT_ID, TOPIC, EVENT_TYPE, "
    f"TIMESTAMP, PAYLOAD, SOURCE, CAUSATION_ID"
    f") VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8)"
)

_SELECT_BY_RUN_SQL = (
    f"SELECT RUN_ID, SEQUENCE, EVENT_ID, TOPIC, EVENT_TYPE, "
    f"TIMESTAMP, PAYLOAD, SOURCE, CAUSATION_ID "
    f"FROM {_TABLE} WHERE RUN_ID = :p0 ORDER BY SEQUENCE"
)

_SELECT_BY_RUN_SINCE_SQL = (
    f"SELECT RUN_ID, SEQUENCE, EVENT_ID, TOPIC, EVENT_TYPE, "
    f"TIMESTAMP, PAYLOAD, SOURCE, CAUSATION_ID "
    f"FROM {_TABLE} WHERE RUN_ID = :p0 AND SEQUENCE > :p1 "
    f"ORDER BY SEQUENCE"
)

_COUNT_BY_RUN_SQL = f"SELECT COUNT(*) AS CNT FROM {_TABLE} WHERE RUN_ID = :p0"

# Position i holds sequence i+1 (contiguous per run), so a page is a
# (RUN_ID, SEQUENCE) range seek — no OFFSET row-scan on large logs.
_SELECT_BY_RUN_PAGE_SQL = (
    f"SELECT RUN_ID, SEQUENCE, EVENT_ID, TOPIC, EVENT_TYPE, "
    f"TIMESTAMP, PAYLOAD, SOURCE, CAUSATION_ID "
    f"FROM {_TABLE} WHERE RUN_ID = :p0 AND SEQUENCE > :p1 AND SEQUENCE <= :p2 "
    f"ORDER BY SEQUENCE"
)

_DELETE_BY_RUN_SQL = f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0"


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaEventLogRepository(AsyncRepoMixin, IEventLogRepository):
    """SAP HANA-backed event log.

    Usage::

        from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
            HanaConnectionManager,
        )

        db = HanaConnectionManager(settings)
        db.initialize()
        event_log = HanaEventLogRepository(db)

    Ships with a fallback for HANA's lack of ``INSERT OR IGNORE`` —
    see the module docstring for the unique-constraint-exception
    dedupe strategy.
    """

    def __init__(self, db: Any) -> None:
        """Construct with an ``IDatabaseConnection`` instance.

        ``db`` must expose ``execute_query(sql, params)`` and
        ``execute_write(sql, params)``. In production this is
        ``HanaConnectionManager``. Tests pass a stub.
        """
        self._db = db

    # ─────────────────────────── append ────────────────────────────

    def append(self, envelope: EventLogEnvelope) -> bool:
        """Persist ``envelope`` to ``AIW_EVENT_LOG``.

        Returns ``True`` if inserted, ``False`` if a row with the
        same ``(RUN_ID, EVENT_ID)`` already exists (detected via the
        ``IDX_AIW_EVENT_LOG_EVENT_ID`` unique index firing with HANA
        error code 301).

        Empty ``run_id`` maps to the ``"__unscoped__"`` bucket to
        match the other backends' contract. Callers SHOULD provide an
        explicit id.
        """
        run_id = envelope.run_id or "__unscoped__"
        payload_json = json.dumps(envelope.payload or {}, default=str)

        rows = self._db.execute_query(_NEXT_SEQUENCE_SQL, (run_id,))
        next_sequence = self._extract_next_sequence(rows)

        try:
            self._db.execute_write(
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
            return True
        except Exception as exc:
            # HANA raises hdbcli.dbapi.Error with ``errorcode``
            # attribute on unique-constraint violations. The unique
            # index on (RUN_ID, EVENT_ID) guarantees this is the
            # dedupe signal.
            if getattr(exc, "errorcode", None) == _HANA_UNIQUE_VIOLATION:
                return False
            raise

    # ────────────────────────── list_by_run ───────────────────────

    def list_by_run(self, run_id: str) -> list[EventLogEnvelope]:
        """Return every envelope logged for ``run_id`` in append order.

        Empty ``run_id`` resolves to the ``"__unscoped__"`` bucket.
        """
        key = run_id or "__unscoped__"
        rows = self._db.execute_query(_SELECT_BY_RUN_SQL, (key,))
        return [self._row_to_envelope(row) for row in rows]

    def list_by_run_since(
        self, run_id: str, min_sequence: int,
    ) -> list[EventLogEnvelope]:
        """Phase 4c.2: return envelopes with ``sequence > min_sequence``.

        Uses ``WHERE SEQUENCE > :p1`` against the ``(RUN_ID, SEQUENCE)``
        primary-key index, so the query is an index seek. The hot path
        for the cached projector store — "cache is warm, no new
        events" — returns an empty list with one index lookup.
        """
        key = run_id or "__unscoped__"
        rows = self._db.execute_query(
            _SELECT_BY_RUN_SINCE_SQL, (key, int(min_sequence)),
        )
        return [self._row_to_envelope(row) for row in rows]

    def count_by_run(self, run_id: str) -> int:
        """Return the number of envelopes logged for ``run_id``."""
        key = run_id or "__unscoped__"
        rows = self._db.execute_query(_COUNT_BY_RUN_SQL, (key,))
        if not rows:
            return 0
        cnt = rows[0].get("cnt", rows[0].get("CNT", 0))
        return int(cnt) if cnt is not None else 0

    def list_by_run_page(
        self, run_id: str, offset: int, limit: int,
    ) -> list[EventLogEnvelope]:
        """Return up to ``limit`` envelopes from 0-based ``offset`` in append
        order, resolved as a ``(RUN_ID, SEQUENCE)`` range seek."""
        key = run_id or "__unscoped__"
        offset = max(0, int(offset))
        limit = max(0, int(limit))
        rows = self._db.execute_query(
            _SELECT_BY_RUN_PAGE_SQL, (key, offset, offset + limit),
        )
        return [self._row_to_envelope(row) for row in rows]

    # ────────────────────────────── clear ─────────────────────────

    def clear(self, run_id: str) -> int:
        """Drop every envelope for ``run_id``.

        Returns the number of rows removed. ``execute_write`` does not
        expose the deleted-row count, so we query the count first and
        use it as the return value — the delete itself is unconditional.
        This matches the contract in the other backends (rowcount
        returned from the memory/SQLite ``clear``).
        """
        key = run_id or "__unscoped__"

        # Count rows first so we can return the expected value.
        count_sql = f"SELECT COUNT(*) AS CNT FROM {_TABLE} WHERE RUN_ID = :p0"
        rows = self._db.execute_query(count_sql, (key,))
        removed = 0
        if rows:
            cnt = rows[0].get("cnt", rows[0].get("CNT", 0))
            removed = int(cnt) if cnt is not None else 0

        if removed > 0:
            self._db.execute_write(_DELETE_BY_RUN_SQL, (key,))
        return removed

    # ────────────────────────── internal helpers ─────────────────

    @staticmethod
    def _extract_next_sequence(rows: list[dict]) -> int:
        """Parse the ``NEXT_SEQ`` column returned by the sequence query.

        HANA's cursor descriptions are lowercased by
        ``HanaConnectionManager.execute_query``, so the column is
        ``"next_seq"``. Accept both casings for the stub tests.
        """
        if not rows:
            return 1
        row = rows[0]
        value = row.get("next_seq", row.get("NEXT_SEQ", 1))
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _row_to_envelope(row: dict) -> EventLogEnvelope:
        """Build an ``EventLogEnvelope`` from a HANA result row.

        Handles both lower-case (``HanaConnectionManager``) and
        upper-case (stub) column names so the repo is backend-agnostic.
        """
        def _get(*keys: str) -> Any:
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        run_id = _get("run_id", "RUN_ID") or ""
        if run_id == "__unscoped__":
            run_id = ""

        sequence_raw = _get("sequence", "SEQUENCE")
        sequence = int(sequence_raw) if sequence_raw is not None else 0

        payload_raw = _get("payload", "PAYLOAD")
        if isinstance(payload_raw, (dict, list)):
            payload = payload_raw
        elif isinstance(payload_raw, str) and payload_raw:
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                logger.warning(
                    "HanaEventLogRepository: failed to decode payload JSON: %r",
                    payload_raw,
                )
                payload = {}
        else:
            payload = {}

        return EventLogEnvelope(
            event_id=str(_get("event_id", "EVENT_ID") or ""),
            run_id=run_id,
            topic=str(_get("topic", "TOPIC") or ""),
            event_type=str(_get("event_type", "EVENT_TYPE") or ""),
            timestamp=str(_get("timestamp", "TIMESTAMP") or ""),
            payload=payload,
            sequence=sequence,
            source=str(_get("source", "SOURCE") or ""),
            causation_id=str(_get("causation_id", "CAUSATION_ID") or ""),
        )
