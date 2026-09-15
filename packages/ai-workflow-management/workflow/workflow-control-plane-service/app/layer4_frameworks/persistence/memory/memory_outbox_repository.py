"""In-memory outbox repository — Phase 3 Sub 3.2.

Wraps an ``IEventLogRepository`` and computes "unresolved" by walking
every run's log: ``OutboxRequested`` envelopes whose ``outbox_id`` is
not paired with a later ``OutboxResolved`` envelope.

Memory backend keeps run ids in a registry so ``list_unresolved`` does
not have to enumerate the entire event-log keyspace — the dispatcher
calls this on a poll loop, so the scan must stay cheap. Each
``mark_run`` call seeds the registry; production code seeds it via
the producer-side hook in ``OutboxAwaiter`` (Sub 3.3) or via the
projector's ``pending_outbox`` field for the cold-start backfill.

For Sub 3.2 the dispatcher exercises this module against ad-hoc test
fixtures — Sub 3.3 will pin the production wiring.
"""

from __future__ import annotations

import threading

from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.outbox_repository_interface import (
    IOutboxRepository,
    OutboxEntry,
)


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryOutboxRepository(AsyncRepoMixin, IOutboxRepository):
    """In-memory outbox repo over a memory event log."""

    def __init__(self, event_log_repo: IEventLogRepository) -> None:
        self._event_log_repo = event_log_repo
        self._known_runs: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def mark_run(self, run_id: str) -> None:
        """Register ``run_id`` for the dispatcher's scan.

        Called by the producer-side hook each time a handler appends
        ``OutboxRequested``. Idempotent.
        """
        if not run_id:
            return
        with self._lock:
            self._known_runs.add(run_id)

    def list_unresolved(
        self, since_sequence: int = 0, limit: int = 100,
    ) -> list[OutboxEntry]:
        with self._lock:
            run_ids = list(self._known_runs)

        out: list[OutboxEntry] = []
        for run_id in run_ids:
            envelopes = self._event_log_repo.list_by_run(run_id)
            resolved: set[str] = {
                str(e.payload.get("outbox_id") or "")
                for e in envelopes
                if e.event_type == "OutboxResolved"
            }
            for envelope in envelopes:
                if envelope.event_type != "OutboxRequested":
                    continue
                if envelope.sequence <= since_sequence:
                    continue
                outbox_id = str(envelope.payload.get("outbox_id") or "")
                if not outbox_id or outbox_id in resolved:
                    continue
                out.append(
                    OutboxEntry(
                        outbox_id=outbox_id,
                        run_id=run_id,
                        workflow_id=str(envelope.payload.get("workflow_id") or ""),
                        kind=str(envelope.payload.get("kind") or ""),
                        payload=dict(envelope.payload.get("payload") or {}),
                        idempotency_key=str(envelope.payload.get("idempotency_key") or ""),
                        sequence=envelope.sequence,
                        requested_at=envelope.timestamp,
                        causation_id=envelope.causation_id,
                    ),
                )
                if len(out) >= limit:
                    return out
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
