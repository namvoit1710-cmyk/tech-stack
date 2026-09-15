"""SAP HANA implementation of ``ITimerRepository`` — SA-1371.

Durable pending-timer store for SAP BTP production deployments. Mirrors the
semantics of ``MemoryTimerRepository`` and ``SqliteTimerRepository`` exactly —
same save/list_due/list_by_run/get/delete contract — but persists to the
``AIW_TIMERS`` table (migration ``049_create_aiw_timers.sql``).

Pattern notes
=============

Follows the HANA repository template used by ``HanaEventLogRepository`` and
``HanaSnapshotRepository``:

- Constructor takes an ``IDatabaseConnection`` (production:
  ``HanaConnectionManager``; tests: stub).
- ``:p0``, ``:p1``, … named placeholders — translated to positional ``?`` by
  the connection manager.
- Upsert via delete-then-insert: HANA has no portable ``ON CONFLICT DO
  UPDATE``, and re-scheduling the same ``timer_id`` is infrequent, so the extra
  delete is negligible.
- ``metadata`` is stored as JSON in an ``NCLOB`` column; ``fire_at`` carries an
  index (``IDX_AIW_TIMERS_FIRE_AT``) so ``list_due``'s ``fire_at <= :p0`` scan
  is an index range read.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.layer1_domain.entities.timer import Timer
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)

logger = logging.getLogger(__name__)

_TABLE = "AIW_TIMERS"

_DELETE_SQL = f"DELETE FROM {_TABLE} WHERE TIMER_ID = :p0"

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ("
    f"TIMER_ID, RUN_ID, WORKFLOW_ID, NODE_ID, KIND, "
    f"FIRE_AT, CREATED_AT, METADATA"
    f") VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7)"
)

_SELECT_DUE_SQL = (
    f"SELECT TIMER_ID, RUN_ID, WORKFLOW_ID, NODE_ID, KIND, "
    f"FIRE_AT, CREATED_AT, METADATA "
    f"FROM {_TABLE} WHERE FIRE_AT <> '' AND FIRE_AT <= :p0 "
    f"ORDER BY FIRE_AT LIMIT :p1"
)

_SELECT_BY_RUN_SQL = (
    f"SELECT TIMER_ID, RUN_ID, WORKFLOW_ID, NODE_ID, KIND, "
    f"FIRE_AT, CREATED_AT, METADATA "
    f"FROM {_TABLE} WHERE RUN_ID = :p0"
)

_SELECT_BY_ID_SQL = (
    f"SELECT TIMER_ID, RUN_ID, WORKFLOW_ID, NODE_ID, KIND, "
    f"FIRE_AT, CREATED_AT, METADATA "
    f"FROM {_TABLE} WHERE TIMER_ID = :p0"
)

_COUNT_BY_ID_SQL = f"SELECT COUNT(*) AS CNT FROM {_TABLE} WHERE TIMER_ID = :p0"


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaTimerRepository(AsyncRepoMixin, ITimerRepository):
    """SAP HANA-backed ``ITimerRepository``."""

    def __init__(self, db: Any) -> None:
        """Construct with an ``IDatabaseConnection`` instance.

        ``db`` must expose ``execute_query(sql, params)`` and
        ``execute_write(sql, params)``. In production this is
        ``HanaConnectionManager``; tests pass a stub.
        """
        self._db = db

    def save(self, timer: Timer) -> None:
        """Upsert ``timer_id`` — delete any prior row then insert."""
        self._db.execute_write(_DELETE_SQL, (timer.timer_id,))
        self._db.execute_write(
            _INSERT_SQL,
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

    def list_due(self, now_iso: str, limit: int = 100) -> list[Timer]:
        rows = self._db.execute_query(_SELECT_DUE_SQL, (now_iso, int(limit)))
        return [self._row_to_timer(row) for row in rows]

    def list_by_run(self, run_id: str) -> list[Timer]:
        rows = self._db.execute_query(_SELECT_BY_RUN_SQL, (run_id,))
        return [self._row_to_timer(row) for row in rows]

    def get(self, timer_id: str) -> Timer | None:
        rows = self._db.execute_query(_SELECT_BY_ID_SQL, (timer_id,))
        if not rows:
            return None
        return self._row_to_timer(rows[0])

    def delete(self, timer_id: str) -> bool:
        """Delete ``timer_id``; return whether a row existed.

        ``execute_write`` does not expose the deleted-row count, so we count
        first and use it as the return signal — matching the row-existed
        contract of the memory/SQLite backends.
        """
        rows = self._db.execute_query(_COUNT_BY_ID_SQL, (timer_id,))
        existed = False
        if rows:
            cnt = rows[0].get("cnt", rows[0].get("CNT", 0))
            existed = bool(int(cnt)) if cnt is not None else False
        if existed:
            self._db.execute_write(_DELETE_SQL, (timer_id,))
        return existed

    @staticmethod
    def _row_to_timer(row: dict) -> Timer:
        """Build a ``Timer`` from a HANA result row.

        Handles both lower-case (``HanaConnectionManager``) and upper-case
        (stub) column names so the repo is backend-agnostic.
        """
        def _get(*keys: str) -> Any:
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        metadata_raw = _get("metadata", "METADATA")
        if isinstance(metadata_raw, dict):
            metadata = metadata_raw
        elif isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                logger.warning(
                    "HanaTimerRepository: failed to decode metadata JSON: %r",
                    metadata_raw,
                )
                metadata = {}
        else:
            metadata = {}

        return Timer(
            timer_id=str(_get("timer_id", "TIMER_ID") or ""),
            run_id=str(_get("run_id", "RUN_ID") or ""),
            workflow_id=str(_get("workflow_id", "WORKFLOW_ID") or ""),
            node_id=str(_get("node_id", "NODE_ID") or ""),
            kind=str(_get("kind", "KIND") or "delay"),
            fire_at=str(_get("fire_at", "FIRE_AT") or ""),
            created_at=str(_get("created_at", "CREATED_AT") or ""),
            metadata=metadata,
        )
