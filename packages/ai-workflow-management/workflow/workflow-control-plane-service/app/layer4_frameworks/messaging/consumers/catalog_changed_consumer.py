"""Consumes ``catalog.changed`` and invalidates the CP node-catalog cache.

When a worker registers or deregisters on the executor, it emits
``catalog.changed`` on the message broker. This consumer reacts by
dropping the stale ``NodeCatalogCache`` entry — the next list call
re-fetches fresh summaries from all three providers.

System-scoped events (workers are system-scoped today) invalidate ALL
tenant entries, because business-node definitions in tenant caches may
wrap the affected system worker. See Phase 1 plan §12.3.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer4_frameworks.caching import NodeCatalogCache


logger = logging.getLogger(__name__)

CATALOG_CHANGED_TOPIC = "catalog.changed"


class CatalogChangedConsumer:
    """Subscribes to catalog.changed; invalidates NodeCatalogCache on each event."""

    def __init__(
        self,
        cache: NodeCatalogCache,
        broker: Optional[IBrokerClient] = None,
        logger_impl: Optional[ILogger] = None,
    ) -> None:
        self._cache = cache
        self._broker = broker
        self._log = logger_impl or logger

    def start(self) -> None:
        """Subscribe to the catalog.changed topic.

        No-op when ``broker`` is None (INFRA_MODE=mock without in-process
        messaging). The cache's 30-second TTL is the fallback.

        Calls ``ensure_topic`` when the broker supports it so the
        underlying Kafka topic exists before the consumer subscribes —
        without this, the first subscribe fails with
        ``UNKNOWN_TOPIC_OR_PART`` until the first publish auto-creates
        it. Event Mesh creates its queue + subscription inside
        ``subscribe_to_topic`` and provides a no-op ``ensure_topic`` so
        the same code path works for both backends.
        """
        if self._broker is None:
            self._log.info(
                "catalog.changed consumer: no broker — TTL-only invalidation",
            )
            return

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(CATALOG_CHANGED_TOPIC)
            except Exception as exc:
                self._log.warning(
                    "Could not auto-create topic 'catalog.changed'",
                    error=str(exc),
                )

        self._broker.subscribe_to_topic(CATALOG_CHANGED_TOPIC, self.handle)

    def handle(self, event: Any) -> None:
        """Broker callback: drop cache entries affected by the event.

        System-scoped events invalidate every tenant because business
        nodes can wrap system workers. Tenant-scoped events (future)
        drop only the tenant's entry.
        """
        tenant_id = None
        if isinstance(event, dict):
            tenant_id = event.get("tenant_id")

        if not tenant_id or tenant_id == "system":
            self._cache.invalidate(None)
            self._log.info(
                "catalog.changed invalidated all tenants",
                action=(event or {}).get("action") if isinstance(event, dict) else None,
                worker_type=(event or {}).get("worker_type") if isinstance(event, dict) else None,
            )
        else:
            self._cache.invalidate(tenant_id)
            self._log.info(
                "catalog.changed invalidated tenant",
                tenant_id=tenant_id,
            )
