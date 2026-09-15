"""In-memory implementation of ``ITimerRepository`` — SA-1371.

Thread-safe dict keyed by ``timer_id``. Used in mock/local infra modes and as
the fixture for the timer unit tests; swapped for the SQLite / HANA backend in
production via the ``TIMER_BACKEND`` selector in ``bootstrap.py``.

Matches the lock pattern used by the other ``layer4_frameworks/persistence/
memory/`` repos — one lock for the whole store, since individual operations are
O(timers) and the pending-timer set is small (bounded by the number of runs
currently sleeping on a delay / SLA / schedule).
"""

from __future__ import annotations

import threading

from app.layer1_domain.entities.timer import Timer
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryTimerRepository(AsyncRepoMixin, ITimerRepository):
    """Thread-safe in-memory durable-timer store."""

    def __init__(self) -> None:
        self._by_id: dict[str, Timer] = {}
        self._lock = threading.Lock()

    def save(self, timer: Timer) -> None:
        with self._lock:
            self._by_id[timer.timer_id] = timer

    def list_due(self, now_iso: str, limit: int = 100) -> list[Timer]:
        with self._lock:
            timers = list(self._by_id.values())
        # Deterministic order (earliest fire_at first) so batching under a
        # ``limit`` drains the most-overdue timers first.
        due = sorted(
            (t for t in timers if t.is_due(now_iso)),
            key=lambda t: t.fire_at,
        )
        return due[:limit]

    def list_by_run(self, run_id: str) -> list[Timer]:
        with self._lock:
            return [t for t in self._by_id.values() if t.run_id == run_id]

    def delete(self, timer_id: str) -> bool:
        with self._lock:
            return self._by_id.pop(timer_id, None) is not None

    def get(self, timer_id: str) -> Timer | None:
        with self._lock:
            return self._by_id.get(timer_id)
