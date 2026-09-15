"""Per-instance task concurrency gate (SA-1530).

``/api/v1/execute-async`` previously accepted an unlimited number of tasks —
every request became a background task, so "this instance is full" was
undefined: the executor kept pouring work in and the autoscaler had no
signal. This counter defines saturation explicitly.

Mirror of the executor's ``ConcurrencyControl`` (same shape, same locking
discipline) so both sides of the dispatch contract read alike. Thread-safe:
acceptance runs on the event loop, but sync handlers execute in
``asyncio.to_thread`` and release from those threads.

Deliberately NOT a queue: durable buffering already lives at the executor's
pending-task store and the Event Mesh above it. An internal accept-queue
would be double-buffering that hides saturation from the autoscaler.
"""

from __future__ import annotations

import threading


class WorkerConcurrency:
    """Bounded in-flight task counter. ``max_concurrent<=0`` = unlimited
    (legacy behaviour — rollout is opt-in per worker type)."""

    def __init__(self, max_concurrent: int = 0) -> None:
        self.max_concurrent = int(max_concurrent)
        self._active = 0
        self._lock = threading.Lock()

    @property
    def limited(self) -> bool:
        return self.max_concurrent > 0

    def acquire(self) -> bool:
        """Atomically claim a slot; False when saturated.

        Check-and-increment under one lock so concurrent acceptances can
        never both observe ``< max`` and both increment past the cap.
        """
        with self._lock:
            if self.max_concurrent <= 0 or self._active < self.max_concurrent:
                self._active += 1
                return True
            return False

    def release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1

    def snapshot(self) -> dict:
        """Observability payload for /ready, /api/v1/info and metrics."""
        with self._lock:
            active = self._active
        return {
            "active": active,
            "max": self.max_concurrent,
            "saturation": (
                round(active / self.max_concurrent, 3) if self.max_concurrent > 0 else 0.0
            ),
        }
