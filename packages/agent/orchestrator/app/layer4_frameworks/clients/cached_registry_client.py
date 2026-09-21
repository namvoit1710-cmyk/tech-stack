"""
CachedRegistryClient — TTL cache wrapper around AgentRegistryClient.

Cache policy:
    - Key: SHA-256 hash of (method_name + arguments)
    - TTL: 30 seconds
    - Invalidation: on health change events via Kafka consumer
"""

import hashlib
import time


class CachedRegistryClient:
    """Decorator wrapping AgentRegistryClient with in-process TTL cache."""

    def __init__(self, delegate, ttl_seconds: float = 30.0):
        self._delegate = delegate
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, any]] = {}


    async def filter_agents(self, **kwargs) -> list:
        key = self._cache_key("filter_agents", "", kwargs)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        result = await self._delegate.filter_agents(**kwargs)
        self._set_cached(key, result)
        return result

    async def get_tool_id_by_name(self, name: str) -> str | None:
        # SA-892: the delegate already caches tool-id-by-name forever (ids are
        # stable), so pass straight through rather than layering the 30s TTL.
        return await self._delegate.get_tool_id_by_name(name)

    def invalidate_all(self):
        self._cache.clear()

    def _cache_key(self, method: str, primary: str, kwargs: dict) -> str:
        raw = f"{method}:{primary}:{sorted(kwargs.items())}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str):
        if key in self._cache:
            ts, value = self._cache[key]
            if time.monotonic() - ts < self._ttl:
                return value
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value):
        self._cache[key] = (time.monotonic(), value)
