"""In-memory implementation of ``ISnapshotRepository`` — Phase 4.1.

Per-run list of snapshots with thread-safe mutation. Used in
mock/local infra modes and as the fixture for unit tests; swapped for
the HANA backend in Phase 4.5.
"""

from __future__ import annotations

import threading

from app.layer2_application.interfaces.snapshot_repository_interface import (
    ISnapshotRepository,
    Snapshot,
)


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemorySnapshotRepository(AsyncRepoMixin, ISnapshotRepository):
    """Thread-safe in-memory snapshot store.

    Matches the lock pattern used by the other ``layer4_frameworks/
    persistence/memory/`` repos — one lock for the whole store, since
    individual operations are O(snapshots_per_run) and snapshot counts
    are bounded by the retention policy (K≈2 per run).
    """

    def __init__(self) -> None:
        self._by_run: dict[str, list[Snapshot]] = {}
        self._lock = threading.Lock()

    def save(self, snapshot: Snapshot) -> None:
        with self._lock:
            bucket = self._by_run.setdefault(snapshot.run_id, [])
            for i, existing in enumerate(bucket):
                if existing.sequence == snapshot.sequence:
                    bucket[i] = snapshot
                    return
            bucket.append(snapshot)

    def load_latest(
        self, run_id: str, *, min_schema_version: int,
    ) -> Snapshot | None:
        with self._lock:
            bucket = self._by_run.get(run_id) or []
            eligible = [
                s for s in bucket if s.schema_version >= min_schema_version
            ]
            if not eligible:
                return None
            return max(eligible, key=lambda s: s.sequence)

    def delete_older_than(self, run_id: str, keep_sequence: int) -> int:
        with self._lock:
            bucket = self._by_run.get(run_id)
            if not bucket:
                return 0
            kept = [s for s in bucket if s.sequence >= keep_sequence]
            removed = len(bucket) - len(kept)
            if removed:
                self._by_run[run_id] = kept
            return removed
