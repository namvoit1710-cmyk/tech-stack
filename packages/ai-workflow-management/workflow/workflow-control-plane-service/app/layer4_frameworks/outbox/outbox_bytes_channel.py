"""Per-pod bytes side-channel for outbox handlers — Phase 3 Sub 3.3.

``OutboxRequested`` payloads carry only metadata (filename, content
hash, content type). The actual bytes — e.g. the mutated CSV that
``file_upload`` will hand to the file service — reach the dispatcher
through this channel, keyed by ``outbox_id``.

Replay safety (see ``docs/design/phase-3-outbox-pattern.md`` §7):
when the channel is empty (cold pod, replay across restart), the
producing handler re-runs and re-populates it. The file-service's
``content_hash`` idempotency dedupes the upload to the same
``file_id``, so the second run is byte-identical with no duplicate
storage.

Bounded by ``max_bytes`` to cap memory pressure under heavy upload
load. Eviction policy: FIFO of bytes age — once the cap is hit,
oldest entries drop. The dispatcher's missing-bytes path raises
``RetryableError`` so a dropped entry just retries on the next
producer run.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class OutboxBytesChannel:
    """Thread-safe ``outbox_id → bytes`` map with a soft size cap."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._store: OrderedDict[str, bytes] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def put(self, outbox_id: str, payload: bytes) -> None:
        if not outbox_id:
            return
        with self._lock:
            existing = self._store.pop(outbox_id, None)
            if existing is not None:
                self._size -= len(existing)
            self._store[outbox_id] = payload
            self._size += len(payload)
            self._evict_locked()

    def pop(self, outbox_id: str) -> bytes | None:
        if not outbox_id:
            return None
        with self._lock:
            payload = self._store.pop(outbox_id, None)
            if payload is not None:
                self._size -= len(payload)
            return payload

    def peek(self, outbox_id: str) -> bytes | None:
        with self._lock:
            return self._store.get(outbox_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._size

    def _evict_locked(self) -> None:
        while self._size > self._max_bytes and self._store:
            _, dropped = self._store.popitem(last=False)
            self._size -= len(dropped)
