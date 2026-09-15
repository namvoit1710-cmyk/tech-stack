"""Phase 3 Sub 3.5 — ExecutorConsumer dedup set.

Bounded LRU keyed on ``(run_id, node_id, attempt)``. The Executor
short-circuits redelivered ``NodeInput`` messages whose key is in the
set so a Kafka rebalance or replay does not double-invoke the handler
on the same pod.

Per-pod state, NOT cross-pod. Cross-pod dedupe is delegated to:
  - The event log's ``(run_id, event_id)`` dedupe for Executor-emitted
    events (executor mode).
  - The outbox layer (Sub 3.3 / 3.4) for side-effect handlers.
  - The handler's own idempotency for pure-output handlers.

A pod restart resets the set; the next pod re-runs any unresolved
``NodeInput``. That is acceptable because: (a) handler invocation is
deterministic for pure-output handlers, and (b) side-effect handlers
have outbox-layer idempotency.

LRU capacity defaults to 10000 entries. Each key is two short ids +
an int — at ~100 bytes per entry, ~1 MB resident.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


_DEFAULT_CAPACITY = 10_000


class ExecutorDedup:
    """Thread-safe bounded LRU set for ``(run_id, node_id, attempt)``."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("ExecutorDedup capacity must be positive")
        self._capacity = capacity
        self._seen: OrderedDict[tuple[str, str, int], None] = OrderedDict()
        self._lock = threading.Lock()

    def already_executed(self, run_id: str, node_id: str, attempt: int = 0) -> bool:
        """Return True iff ``(run_id, node_id, attempt)`` is in the LRU.

        Touching a hit moves it to the MRU end so the LRU eviction
        chooses the truly oldest key on overflow.
        """
        key = (run_id, node_id, attempt)
        with self._lock:
            if key in self._seen:
                self._seen.move_to_end(key)
                return True
            return False

    def mark_executed(self, run_id: str, node_id: str, attempt: int = 0) -> None:
        """Insert ``(run_id, node_id, attempt)``; evict oldest on overflow."""
        key = (run_id, node_id, attempt)
        with self._lock:
            if key in self._seen:
                self._seen.move_to_end(key)
                return
            self._seen[key] = None
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()
