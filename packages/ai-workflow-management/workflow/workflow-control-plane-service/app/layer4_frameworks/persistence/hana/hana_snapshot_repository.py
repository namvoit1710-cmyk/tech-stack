"""SAP HANA implementation of ``ISnapshotRepository`` — Phase 4.5.

Durable projector-snapshot backing for SAP BTP deployments. Mirrors
the semantics of ``MemorySnapshotRepository`` exactly; persists to the
``AIW_RUN_SNAPSHOTS`` table introduced in migration 028.

Pattern notes
=============

Follows the HANA repository template used by ``HanaEventLogRepository``:

- Constructor takes an ``IDatabaseConnection`` (production:
  ``HanaConnectionManager``; tests: stub).
- ``:p0``, ``:p1``, … named placeholders — translated to positional
  ``?`` by the connection manager.
- Upsert via delete-then-insert: HANA has no portable ``ON CONFLICT
  DO UPDATE`` and ``MERGE`` requires a source subquery. Snapshot writes
  are infrequent (every 100 events or 5 min), so the cost of the
  delete is negligible compared to the JSON write of ``STATE_JSON``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer2_application.interfaces.snapshot_repository_interface import (
    ISnapshotRepository,
    Snapshot,
)

logger = logging.getLogger(__name__)

_TABLE = "AIW_RUN_SNAPSHOTS"

_DELETE_AT_SEQ_SQL = (
    f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0 AND SEQUENCE = :p1"
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} "
    f"(RUN_ID, SEQUENCE, SCHEMA_VERSION, STATE_JSON, CREATED_AT) "
    f"VALUES (:p0, :p1, :p2, :p3, :p4)"
)

_LOAD_LATEST_SQL = (
    f"SELECT RUN_ID, SEQUENCE, SCHEMA_VERSION, STATE_JSON, CREATED_AT "
    f"FROM {_TABLE} "
    f"WHERE RUN_ID = :p0 AND SCHEMA_VERSION >= :p1 "
    f"ORDER BY SEQUENCE DESC"
)

_DELETE_OLDER_SQL = (
    f"DELETE FROM {_TABLE} WHERE RUN_ID = :p0 AND SEQUENCE < :p1"
)

_COUNT_OLDER_SQL = (
    f"SELECT COUNT(*) AS CNT FROM {_TABLE} "
    f"WHERE RUN_ID = :p0 AND SEQUENCE < :p1"
)


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaSnapshotRepository(AsyncRepoMixin, ISnapshotRepository):
    """SAP HANA-backed ``ISnapshotRepository``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def save(self, snapshot: Snapshot) -> None:
        """Upsert ``(run_id, sequence)`` — delete any prior row then insert."""
        self._db.execute_write(
            _DELETE_AT_SEQ_SQL, (snapshot.run_id, int(snapshot.sequence)),
        )
        self._db.execute_write(
            _INSERT_SQL,
            (
                snapshot.run_id,
                int(snapshot.sequence),
                int(snapshot.schema_version),
                snapshot.state_json,
                snapshot.created_at,
            ),
        )

    def load_latest(
        self, run_id: str, *, min_schema_version: int,
    ) -> Snapshot | None:
        rows = self._db.execute_query(
            _LOAD_LATEST_SQL, (run_id, int(min_schema_version)),
        )
        if not rows:
            return None
        return self._row_to_snapshot(rows[0])

    def delete_older_than(self, run_id: str, keep_sequence: int) -> int:
        rows = self._db.execute_query(
            _COUNT_OLDER_SQL, (run_id, int(keep_sequence)),
        )
        removed = 0
        if rows:
            cnt = rows[0].get("cnt", rows[0].get("CNT", 0))
            removed = int(cnt) if cnt is not None else 0
        if removed > 0:
            self._db.execute_write(
                _DELETE_OLDER_SQL, (run_id, int(keep_sequence)),
            )
        return removed

    @staticmethod
    def _row_to_snapshot(row: dict) -> Snapshot:
        def _get(*keys: str) -> Any:
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        return Snapshot(
            run_id=str(_get("run_id", "RUN_ID") or ""),
            sequence=int(_get("sequence", "SEQUENCE") or 0),
            schema_version=int(_get("schema_version", "SCHEMA_VERSION") or 0),
            state_json=str(_get("state_json", "STATE_JSON") or ""),
            created_at=str(_get("created_at", "CREATED_AT") or ""),
        )
