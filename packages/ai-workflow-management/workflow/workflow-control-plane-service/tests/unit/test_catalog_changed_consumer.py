"""Tests for CatalogChangedConsumer — broker topic → cache invalidation."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from app.layer4_frameworks.caching import NodeCatalogCache
from app.layer4_frameworks.messaging.consumers.catalog_changed_consumer import (
    CATALOG_CHANGED_TOPIC,
    CatalogChangedConsumer,
)


class _FakeBroker:
    """Captures subscribe calls and lets tests inject events via dispatch()."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, Callable[[Any], None]] = {}

    def subscribe_to_topic(self, topic: str, callback: Callable[[Any], None]) -> None:
        self.subscriptions[topic] = callback

    def dispatch(self, topic: str, event: Any) -> None:
        self.subscriptions[topic](event)


class _Logger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class TestSubscription:
    def test_start_subscribes_to_catalog_changed(self):
        broker = _FakeBroker()
        cache = NodeCatalogCache()
        consumer = CatalogChangedConsumer(cache=cache, broker=broker, logger_impl=_Logger())
        consumer.start()
        assert CATALOG_CHANGED_TOPIC in broker.subscriptions

    def test_start_noop_when_no_broker(self):
        cache = NodeCatalogCache()
        consumer = CatalogChangedConsumer(cache=cache, broker=None, logger_impl=_Logger())
        consumer.start()  # must not raise


class TestInvalidation:
    def _arm(self) -> tuple[NodeCatalogCache, CatalogChangedConsumer, _FakeBroker]:
        cache = NodeCatalogCache()
        broker = _FakeBroker()
        consumer = CatalogChangedConsumer(cache=cache, broker=broker, logger_impl=_Logger())
        consumer.start()
        return cache, consumer, broker

    def test_system_event_invalidates_all_tenants(self):
        cache, _, broker = self._arm()
        cache.put("system", result="s", etag="eS")
        cache.put("tenant-a", result="a", etag="eA")
        cache.put("tenant-b", result="b", etag="eB")

        broker.dispatch(CATALOG_CHANGED_TOPIC, {
            "action": "registered",
            "worker_type": "http",
            "tenant_id": "system",
        })

        assert cache.get("system") is None
        assert cache.get("tenant-a") is None
        assert cache.get("tenant-b") is None

    def test_tenant_scoped_event_invalidates_only_that_tenant(self):
        cache, _, broker = self._arm()
        cache.put("system", result="s", etag="eS")
        cache.put("tenant-a", result="a", etag="eA")
        cache.put("tenant-b", result="b", etag="eB")

        broker.dispatch(CATALOG_CHANGED_TOPIC, {
            "action": "registered",
            "worker_type": "http",
            "tenant_id": "tenant-a",
        })

        assert cache.get("tenant-a") is None
        # Other tenants retain their entries.
        assert cache.get("system") is not None
        assert cache.get("tenant-b") is not None

    def test_missing_tenant_id_treated_as_system(self):
        cache, _, broker = self._arm()
        cache.put("tenant-a", result="a", etag="eA")
        broker.dispatch(CATALOG_CHANGED_TOPIC, {"action": "registered"})
        assert cache.get("tenant-a") is None

    def test_non_dict_event_treated_as_system(self):
        cache, _, broker = self._arm()
        cache.put("tenant-a", result="a", etag="eA")
        broker.dispatch(CATALOG_CHANGED_TOPIC, "unexpected-string-payload")
        assert cache.get("tenant-a") is None


class TestHandleDirect:
    """``handle()`` is called directly in tests without routing through broker."""

    def test_direct_handle_invalidates(self):
        cache = NodeCatalogCache()
        cache.put("tenant-z", result="z", etag="eZ")
        consumer = CatalogChangedConsumer(cache=cache, broker=None, logger_impl=_Logger())
        consumer.handle({"action": "registered", "tenant_id": "tenant-z"})
        assert cache.get("tenant-z") is None
