"""Thread-safe in-memory implementation of IPendingTaskStore."""

import threading
from datetime import datetime, timezone
from typing import Optional

from app.layer2_application.interfaces.pending_task_store_interface import IPendingTaskStore, PendingTaskEntry


class MemoryPendingTaskStore(IPendingTaskStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, PendingTaskEntry] = {}

    def store(self, entry: PendingTaskEntry) -> None:
        with self._lock:
            self._store[entry.task_id] = entry

    def store_if_absent(self, entry: PendingTaskEntry) -> bool:
        """Atomically claim *entry* iff its task_id is not already in flight.

        Returns True if stored (newly claimed), False if a task with the same
        id is already present (an at-least-once redelivery). Check + insert
        under one lock so two consumer threads can't both claim the same task.
        """
        with self._lock:
            if entry.task_id in self._store:
                return False
            self._store[entry.task_id] = entry
            return True

    def get(self, task_id: str) -> Optional[PendingTaskEntry]:
        with self._lock:
            return self._store.get(task_id)

    def remove(self, task_id: str) -> Optional[PendingTaskEntry]:
        with self._lock:
            return self._store.pop(task_id, None)

    def get_timed_out(self) -> list[PendingTaskEntry]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            return [e for e in self._store.values() if e.timeout_at <= now]

    def count(self) -> int:
        with self._lock:
            return len(self._store)
