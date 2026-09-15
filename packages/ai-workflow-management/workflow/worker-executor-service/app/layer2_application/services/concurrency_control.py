"""Thread-safe in-flight task counter.

Accessed concurrently from multiple threads: the broker poll thread(s)
(``EventMeshBrokerClient._poll_loop``), and FastAPI's sync-route threadpool
(``POST /tasks/execute`` acquires, ``POST /tasks/callback`` and the sweep
release). The counter MUST be guarded — an unlocked read-modify-write lets
``acquire`` exceed ``max_concurrent`` (check-then-act) and concurrent releases
corrupt the count.
"""

import threading


class ConcurrencyControl:
    def __init__(self, max_concurrent: int = 10) -> None:
        self.max_concurrent = max_concurrent
        self._active_count = 0
        self._lock = threading.Lock()

    def can_accept(self) -> bool:
        with self._lock:
            return self._active_count < self.max_concurrent

    def acquire(self) -> bool:
        """Atomically claim a slot. Returns False if at capacity.

        Check-and-increment under one lock so concurrent callers can never both
        observe ``< max`` and both increment past the cap.
        """
        with self._lock:
            if self._active_count < self.max_concurrent:
                self._active_count += 1
                return True
            return False

    def release(self) -> None:
        with self._lock:
            if self._active_count > 0:
                self._active_count -= 1

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count
