"""In-memory per-tenant cache for the /v2/node-catalog list response.

Phase 1 uses a process-local dict. Each CP instance keeps its own copy;
cross-instance consistency is eventual via the ``catalog.changed`` event
(Task 1.6). TTL is the fallback when an event is missed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional


@dataclass
class CachedCatalogPayload:
    result: Any
    etag: str
    expires_at_epoch_ms: int


def _now_ms() -> int:
    return int(time.time() * 1000)


class NodeCatalogCache:
    """Thread-safe in-memory cache, keyed by tenant_id."""

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl_ms = max(1, ttl_seconds) * 1000
        self._store: dict[str, CachedCatalogPayload] = {}
        self._lock = Lock()

    def get(self, tenant_id: str) -> Optional[CachedCatalogPayload]:
        with self._lock:
            entry = self._store.get(tenant_id)
            if entry is None:
                return None
            if entry.expires_at_epoch_ms < _now_ms():
                self._store.pop(tenant_id, None)
                return None
            return entry

    def put(self, tenant_id: str, result: Any, etag: str) -> None:
        with self._lock:
            self._store[tenant_id] = CachedCatalogPayload(
                result=result,
                etag=etag,
                expires_at_epoch_ms=_now_ms() + self._ttl_ms,
            )

    def invalidate(self, tenant_id: Optional[str] = None) -> None:
        """Drop one tenant's entry, or all if ``tenant_id`` is None."""
        with self._lock:
            if tenant_id is None:
                self._store.clear()
            else:
                self._store.pop(tenant_id, None)
