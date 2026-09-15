"""In-memory implementation of ``IEventLogRepository`` — Phase 4b.1.

Per-run append-only list with dedupe by ``(run_id, event_id)`` and a
monotonic ``sequence`` counter. Swapped for a persistent backend
(SQLite / HANA) in a later phase.
"""

from __future__ import annotations

import dataclasses
import threading

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
    IEventLogRepository,
)


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryEventLogRepository(AsyncRepoMixin, IEventLogRepository):
    """Thread-safe in-memory event log.

    The existing engine is single-process and single-threaded per run, but
    tests and the drain loop may call append from different contexts, so
    a lock is cheap insurance — matches the pattern used by the other
    memory repositories in ``layer4_frameworks/persistence/memory/``.
    """

    def __init__(self) -> None:
        self._by_run: dict[str, list[EventLogEnvelope]] = {}
        self._seen_ids: dict[str, set[str]] = {}
        self._sequence: dict[str, int] = {}
        self._lock = threading.Lock()

    def append(self, envelope: EventLogEnvelope) -> bool:
        run_id = envelope.run_id or "__unscoped__"
        with self._lock:
            seen = self._seen_ids.setdefault(run_id, set())
            if envelope.event_id and envelope.event_id in seen:
                return False
            next_seq = self._sequence.get(run_id, 0) + 1
            self._sequence[run_id] = next_seq
            stored = dataclasses.replace(envelope, sequence=next_seq)
            self._by_run.setdefault(run_id, []).append(stored)
            if envelope.event_id:
                seen.add(envelope.event_id)
            return True

    def list_by_run(self, run_id: str) -> list[EventLogEnvelope]:
        key = run_id or "__unscoped__"
        with self._lock:
            return list(self._by_run.get(key, ()))

    def list_by_run_since(
        self, run_id: str, min_sequence: int,
    ) -> list[EventLogEnvelope]:
        """Phase 4c.2: filter per-run list by ``sequence > min_sequence``.

        O(|events_in_run|) per call — acceptable because the cached
        projector only hits this path when called for a run whose
        cache is about to be incrementally extended, and the hot path
        returns an empty list quickly.
        """
        key = run_id or "__unscoped__"
        with self._lock:
            return [
                envelope
                for envelope in self._by_run.get(key, ())
                if envelope.sequence > min_sequence
            ]

    def count_by_run(self, run_id: str) -> int:
        key = run_id or "__unscoped__"
        with self._lock:
            return len(self._by_run.get(key, ()))

    def list_by_run_page(
        self, run_id: str, offset: int, limit: int,
    ) -> list[EventLogEnvelope]:
        key = run_id or "__unscoped__"
        offset = max(0, offset)
        limit = max(0, limit)
        with self._lock:
            return list(self._by_run.get(key, ())[offset:offset + limit])

    def clear(self, run_id: str) -> int:
        key = run_id or "__unscoped__"
        with self._lock:
            removed = len(self._by_run.pop(key, ()))
            self._seen_ids.pop(key, None)
            self._sequence.pop(key, None)
            return removed
