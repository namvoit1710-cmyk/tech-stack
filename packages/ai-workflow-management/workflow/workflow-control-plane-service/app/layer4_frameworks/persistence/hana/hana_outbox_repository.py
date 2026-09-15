"""SAP HANA outbox repository — Phase 3 Sub 3.2.

Piggybacks on ``AIW_EVENT_LOG`` as the storage of record. ``OutboxRequested``
and ``OutboxResolved`` events are ordinary log rows; "unresolved" is
materialised by an anti-join on ``(RUN_ID, OUTBOX_ID)``.

The attempt counter is in-memory per pod (matches
``MemoryOutboxRepository``); a pod restart resets it. Acceptable
because handler perform calls use idempotency keys — duplicate
attempts are safe.

Schema is unchanged from Phase 4b.8 (``AIW_EVENT_LOG``). No new
migration is required for Sub 3.2 itself; the table already carries
``EVENT_TYPE`` and a JSON ``PAYLOAD`` column that we filter and parse.

Note on ``OUTBOX_ID`` extraction: HANA's ``JSON_VALUE`` is the
documented path. Older HANA versions before 2 SPS 03 lack it; the
fallback is ``LIKE`` matching on the serialised JSON, which is what
this implementation uses to stay portable across the HANA versions
the project supports today. The query is bounded by ``EVENT_TYPE``
and ``LIMIT`` so the LIKE scan is acceptable.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from app.layer2_application.interfaces.outbox_repository_interface import (
    IOutboxRepository,
    OutboxEntry,
)


logger = logging.getLogger(__name__)


_TABLE = "AIW_EVENT_LOG"

_SELECT_REQUESTED_SQL = (
    f"SELECT RUN_ID, SEQUENCE, EVENT_ID, TIMESTAMP, PAYLOAD, CAUSATION_ID "
    f"FROM {_TABLE} "
    f"WHERE EVENT_TYPE = 'OutboxRequested' AND SEQUENCE > :p0 "
    f"ORDER BY SEQUENCE LIMIT :p1"
)

_SELECT_RESOLVED_IDS_SQL = (
    f"SELECT PAYLOAD FROM {_TABLE} "
    f"WHERE EVENT_TYPE = 'OutboxResolved' AND RUN_ID = :p0"
)


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaOutboxRepository(AsyncRepoMixin, IOutboxRepository):
    """HANA-backed outbox repo. Reads from ``AIW_EVENT_LOG``."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def list_unresolved(
        self, since_sequence: int = 0, limit: int = 100,
    ) -> list[OutboxEntry]:
        try:
            rows = self._db.execute_query(
                _SELECT_REQUESTED_SQL, (since_sequence, max(1, limit) * 2),
            )
        except Exception:
            logger.exception("hana outbox list_unresolved query failed")
            return []

        entries: list[OutboxEntry] = []
        seen_resolved_by_run: dict[str, set[str]] = {}
        for row in rows:
            run_id = str(row[0])
            sequence = int(row[1])
            timestamp = str(row[3] or "")
            causation_id = str(row[5] or "")
            payload = _parse_payload(row[4])
            outbox_id = str(payload.get("outbox_id") or "")
            if not outbox_id:
                continue

            resolved_ids = seen_resolved_by_run.get(run_id)
            if resolved_ids is None:
                resolved_ids = self._load_resolved_ids(run_id)
                seen_resolved_by_run[run_id] = resolved_ids
            if outbox_id in resolved_ids:
                continue

            entries.append(
                OutboxEntry(
                    outbox_id=outbox_id,
                    run_id=run_id,
                    workflow_id=str(payload.get("workflow_id") or ""),
                    kind=str(payload.get("kind") or ""),
                    payload=dict(payload.get("payload") or {}),
                    idempotency_key=str(payload.get("idempotency_key") or ""),
                    sequence=sequence,
                    requested_at=timestamp,
                    causation_id=causation_id,
                ),
            )
            if len(entries) >= limit:
                break
        return entries

    def _load_resolved_ids(self, run_id: str) -> set[str]:
        try:
            rows = self._db.execute_query(_SELECT_RESOLVED_IDS_SQL, (run_id,))
        except Exception:
            logger.exception(
                "hana outbox resolved-id query failed for run_id=%s", run_id,
            )
            return set()
        out: set[str] = set()
        for row in rows:
            payload = _parse_payload(row[0])
            outbox_id = str(payload.get("outbox_id") or "")
            if outbox_id:
                out.add(outbox_id)
        return out

    def record_attempt(self, outbox_id: str) -> int:
        with self._lock:
            self._attempts[outbox_id] = self._attempts.get(outbox_id, 0) + 1
            return self._attempts[outbox_id]

    def get_attempts(self, outbox_id: str) -> int:
        with self._lock:
            return self._attempts.get(outbox_id, 0)

    def reset_attempts(self, outbox_id: str) -> None:
        with self._lock:
            self._attempts.pop(outbox_id, None)
