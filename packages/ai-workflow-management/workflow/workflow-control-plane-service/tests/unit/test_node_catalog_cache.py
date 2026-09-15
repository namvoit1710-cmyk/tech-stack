"""Tests for NodeCatalogCache — per-tenant in-memory cache with TTL."""

import time

import pytest

from app.layer4_frameworks.caching import NodeCatalogCache


class TestCacheHitsAndMisses:
    def test_miss_on_empty_cache(self):
        cache = NodeCatalogCache(ttl_seconds=30)
        assert cache.get("system") is None

    def test_hit_after_put(self):
        cache = NodeCatalogCache(ttl_seconds=30)
        cache.put("system", result="payload", etag='W/"abc"')
        entry = cache.get("system")
        assert entry is not None
        assert entry.result == "payload"
        assert entry.etag == 'W/"abc"'

    def test_miss_after_ttl_expires(self, monkeypatch):
        cache = NodeCatalogCache(ttl_seconds=1)
        cache.put("system", result="p1", etag="e1")
        # Fast-forward time by patching _now_ms on the module.
        import app.layer4_frameworks.caching.node_catalog_cache as mod
        monkeypatch.setattr(mod, "_now_ms", lambda: int(time.time() * 1000) + 2000)
        assert cache.get("system") is None


class TestPerTenantKeying:
    def test_two_tenants_keep_separate_entries(self):
        cache = NodeCatalogCache()
        cache.put("t-a", result="A", etag="eA")
        cache.put("t-b", result="B", etag="eB")
        assert cache.get("t-a").result == "A"
        assert cache.get("t-b").result == "B"

    def test_invalidate_single_tenant_leaves_others(self):
        cache = NodeCatalogCache()
        cache.put("t-a", result="A", etag="eA")
        cache.put("t-b", result="B", etag="eB")
        cache.invalidate("t-a")
        assert cache.get("t-a") is None
        assert cache.get("t-b").result == "B"

    def test_invalidate_none_clears_all(self):
        cache = NodeCatalogCache()
        cache.put("t-a", result="A", etag="eA")
        cache.put("t-b", result="B", etag="eB")
        cache.invalidate(None)
        assert cache.get("t-a") is None
        assert cache.get("t-b") is None

    def test_invalidate_unknown_tenant_is_noop(self):
        cache = NodeCatalogCache()
        cache.put("t-a", result="A", etag="eA")
        cache.invalidate("never-put")
        assert cache.get("t-a").result == "A"


class TestPutOverwrites:
    def test_second_put_replaces_first(self):
        cache = NodeCatalogCache()
        cache.put("t", result="old", etag="e1")
        cache.put("t", result="new", etag="e2")
        entry = cache.get("t")
        assert entry.result == "new"
        assert entry.etag == "e2"
