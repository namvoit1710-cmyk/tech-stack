from __future__ import annotations

import time
from collections import OrderedDict


class MessageDeduplicator:
    def __init__(self, *, max_size: int = 10_000, ttl_seconds: float = 300.0) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl
        while self._seen:
            oldest_key, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts <= cutoff:
                self._seen.pop(oldest_key)
            else:
                break

    def is_duplicate(self, message_id: str) -> bool:
        self._evict_expired()
        ts = self._seen.get(message_id)
        if ts is None:
            return False
        if time.monotonic() - ts > self._ttl:
            self._seen.pop(message_id, None)
            return False
        return True

    def mark_seen(self, message_id: str) -> None:
        self._evict_expired()
        self._seen.pop(message_id, None)
        self._seen[message_id] = time.monotonic()
        while len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
