"""SAP HANA implementation of ``IWorkerRegistryMirrorRepository`` — SA-2050 (R3).

Durable, human-queryable MIRROR of the executor's in-memory worker registry. The
CP background loop snapshot-reconciles ``list_workers()`` into
``AIW_WORKER_REGISTRY_MIRROR`` (migration ``051``) so an operator can see the
live replicas in DBeaver / SQL. Read-only for humans — nothing reads this table
for dispatch or the palette. See ``sa-2050-r3-worker-registry-mirror-spec.md``.

Pattern notes (clones ``HanaTimerRepository``)
=============================================
- Constructor takes an ``IDatabaseConnection`` (prod: ``HanaConnectionManager``;
  tests: a SQLite-backed stub).
- ``:p0``, ``:p1``, … named placeholders — translated to ``?`` by the manager.
- Upsert via delete-then-insert (HANA has no portable ``ON CONFLICT``),
  preserving ``FIRST_SEEN_AT`` across reconciles; ``LAST_SEEN_AT`` = now each pass.
- ``instance_id`` is derived from the PK ``id`` (executor contract
  ``id = f"{instance_id}:{worker_type}"``; local uuid has no colon → id itself).
- Defensive ``_row_to_dict`` (``HanaConnectionManager`` lowercases columns).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.worker_registry_mirror_repository_interface import (
    IWorkerRegistryMirrorRepository,
    ReconcileResult,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin

logger = logging.getLogger(__name__)

_TABLE = "AIW_WORKER_REGISTRY_MIRROR"

# Column order is load-bearing: the INSERT param tuple in ``_insert_params`` MUST
# match this order exactly.
_COLUMNS = (
    "ID", "INSTANCE_ID", "WORKER_TYPE", "HEALTH_STATUS", "LAST_HEARTBEAT",
    "ENDPOINT_URL", "VERSION", "SPEC_VERSION", "KIND", "HOST", "PID",
    "STARTED_AT", "FIRST_SEEN_AT", "LAST_SEEN_AT",
)

_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES ("
    + ", ".join(f":p{i}" for i in range(len(_COLUMNS)))
    + ")"
)
_DELETE_ONE_SQL = f"DELETE FROM {_TABLE} WHERE ID = :p0"
_SELECT_FIRST_SEEN_SQL = f"SELECT FIRST_SEEN_AT FROM {_TABLE} WHERE ID = :p0"
_SELECT_ALL_SQL = f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}"


class HanaWorkerRegistryMirrorRepository(AsyncRepoMixin, IWorkerRegistryMirrorRepository):
    """SAP HANA-backed ``IWorkerRegistryMirrorRepository``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def reconcile(self, rows: list[dict]) -> ReconcileResult:
        """Upsert each present row keyed by ``id``. Empty ``rows`` → no-op.

        Rows whose ``id`` is absent from this snapshot are deleted (P1
        current-state) via ``_delete_absent`` — but only when the snapshot is
        non-empty; an empty snapshot short-circuits above and never deletes.
        """
        if not rows:
            return ReconcileResult(upserted=0, deleted=0)
        now = datetime.now(timezone.utc).isoformat()
        seen_ids: list[str] = []
        upserted = 0
        for row in rows:
            rid = self._row_id(row)
            if not rid:
                continue
            seen_ids.append(rid)
            first_seen = self._existing_first_seen(rid) or now
            self._db.execute_write(_DELETE_ONE_SQL, (rid,))
            self._db.execute_write(
                _INSERT_SQL, self._insert_params(row, rid, first_seen, now),
            )
            upserted += 1
        deleted = self._delete_absent(seen_ids)
        return ReconcileResult(upserted=upserted, deleted=deleted)

    def _delete_absent(self, keep_ids: list[str]) -> int:
        """DELETE rows whose ID is not in ``keep_ids`` (P1 current-state).

        Guarded: an empty ``keep_ids`` deletes nothing — reconcile already
        short-circuits on an empty snapshot, and an all-blank-id snapshot must
        likewise never wipe the mirror. ``execute_write`` exposes no rowcount,
        so we COUNT first to report ``deleted`` (matching the timer repo idiom).
        """
        if not keep_ids:
            return 0
        placeholders = ", ".join(f":p{i}" for i in range(len(keep_ids)))
        count_sql = (
            f"SELECT COUNT(*) AS CNT FROM {_TABLE} WHERE ID NOT IN ({placeholders})"
        )
        rows = self._db.execute_query(count_sql, tuple(keep_ids))
        deleted = 0
        if rows:
            r = rows[0]
            cnt = r.get("cnt", r.get("CNT", 0))
            deleted = int(cnt) if cnt is not None else 0
        del_sql = f"DELETE FROM {_TABLE} WHERE ID NOT IN ({placeholders})"
        self._db.execute_write(del_sql, tuple(keep_ids))
        return deleted

    def get_all(self) -> list[dict]:
        rows = self._db.execute_query(_SELECT_ALL_SQL, ())
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------

    @staticmethod
    def _row_id(row: dict) -> str:
        return str((row or {}).get("id") or "").strip()

    @staticmethod
    def _derive_instance_id(rid: str) -> str:
        return rid.rsplit(":", 1)[0] if ":" in rid else rid

    def _existing_first_seen(self, rid: str) -> str | None:
        rows = self._db.execute_query(_SELECT_FIRST_SEEN_SQL, (rid,))
        if not rows:
            return None
        r = rows[0]
        val = r.get("first_seen_at", r.get("FIRST_SEEN_AT"))
        return str(val) if val else None

    def _insert_params(self, row: dict, rid: str, first_seen: str, now: str) -> tuple:
        def _s(key: str) -> Any:
            v = row.get(key)
            return str(v) if v is not None else None

        pid_raw = row.get("pid")
        try:
            pid = int(pid_raw) if pid_raw is not None else None
        except (ValueError, TypeError):
            pid = None
        return (
            rid,
            self._derive_instance_id(rid),
            _s("worker_type"),
            _s("health_status"),
            _s("last_heartbeat"),
            _s("endpoint_url"),
            _s("version"),
            _s("spec_version"),
            _s("kind"),
            _s("host"),
            pid,
            _s("started_at"),
            first_seen,
            now,
        )

    @staticmethod
    def _row_to_dict(row: dict) -> dict:
        def _get(*keys: str) -> Any:
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        return {
            "id": _get("id", "ID"),
            "instance_id": _get("instance_id", "INSTANCE_ID"),
            "worker_type": _get("worker_type", "WORKER_TYPE"),
            "health_status": _get("health_status", "HEALTH_STATUS"),
            "last_heartbeat": _get("last_heartbeat", "LAST_HEARTBEAT"),
            "endpoint_url": _get("endpoint_url", "ENDPOINT_URL"),
            "version": _get("version", "VERSION"),
            "spec_version": _get("spec_version", "SPEC_VERSION"),
            "kind": _get("kind", "KIND"),
            "host": _get("host", "HOST"),
            "pid": _get("pid", "PID"),
            "started_at": _get("started_at", "STARTED_AT"),
            "first_seen_at": _get("first_seen_at", "FIRST_SEEN_AT"),
            "last_seen_at": _get("last_seen_at", "LAST_SEEN_AT"),
        }
