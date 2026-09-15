"""SAP Event Mesh implementation of a message broker client.

Uses the Event Mesh REST API with OAuth2 client-credentials authentication.
Provides publish/subscribe and queue-based consume/acknowledge semantics.

This module is a thin facade that composes the focused components and exposes
the ``IQueueBrokerClient`` surface by delegation:

* ``EventMeshNames``         — namespace qualification + URL encoding
* ``EventMeshTokenProvider`` — OAuth2 token cache (sync + async refresh)
* ``EventMeshAdmin``         — management API (queues + subscriptions)
* ``EventMeshPublisher``     — async send buffer / flusher / circuit / DLQ
* ``EventMeshConsumer``      — AMQP-push / REST-poll consume + dispatch
* ``EventMeshHttpClients``   — the shared sync + async HTTP pools

SAP Event Mesh API reference:
- Messaging: POST /messagingrest/v1/topics/{topic}/messages
- Queue consume: POST /messagingrest/v1/queues/{queue}/messages/consumption
- Acknowledge: POST /messagingrest/v1/queues/{queue}/messages/{id}/acknowledgement
- Management: /hub/rest/api/v1/management/messaging/queues/...
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from collections.abc import Callable

import httpx

from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_http import EventMeshHttpClients
from app.layer4_frameworks.messaging.event_mesh_token_provider import (
    EventMeshTokenProvider,
)
from app.layer4_frameworks.messaging.event_mesh_admin import EventMeshAdmin
from app.layer4_frameworks.messaging.event_mesh_publisher_pump import EventMeshPublisher
from app.layer4_frameworks.messaging.event_mesh_consumer import EventMeshConsumer
from app.layer2_application.interfaces.broker_client_interface import IQueueBrokerClient

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


class EventMeshBrokerClient(IQueueBrokerClient):
    """Message broker backed by SAP Event Mesh — composes the components above."""

    def __init__(self, config: EventMeshConfig, monitor: Any = None) -> None:
        # ``config`` carries already-validated, already-normalized values (see
        # EventMeshConfig.__post_init__). The facade wires the components; each
        # owns one concern and shares the namespace/token/HTTP infrastructure.
        self._config = config
        self._metrics = EventMeshMetrics(monitor)

        # Mutable namespace (create_queue may adopt it from a 403); shared so
        # publisher/consumer/admin all qualify against the same value.
        # Adoption is lock-guarded; self-heal is config-gated (SA-1468).
        self._names = EventMeshNames(
            config.namespace, allow_self_heal=config.allow_namespace_self_heal,
        )

        # Shared HTTP pools: sync (token + management + consume/ack) now; the
        # async pool is created by the publisher's start().
        self._clients = EventMeshHttpClients(sync=httpx.Client(timeout=config.timeout))

        self._tokens = EventMeshTokenProvider(
            token_url=config.token_url,
            client_id=config.client_id,
            client_secret=config.client_secret,
            clients=self._clients,
            metrics=self._metrics,
        )
        self._admin = EventMeshAdmin(
            management_url=config.management_url,
            names=self._names,
            tokens=self._tokens,
            clients=self._clients,
            lock_duration_ms=config.lock_duration_ms,
            dmq_enabled=config.dmq_enabled,
            dmq_suffix=config.dmq_suffix,
            max_redelivery_count=config.max_redelivery_count,
            consume_transport=config.consume_transport,
        )
        self._publisher = EventMeshPublisher(
            messaging_url=config.messaging_url,
            names=self._names,
            tokens=self._tokens,
            clients=self._clients,
            metrics=self._metrics,
            timeout=config.timeout,
            max_retries=config.max_retries,
            max_payload_bytes=config.max_payload_bytes,
            buffer_size=config.buffer_size,
            buffer_policy=config.buffer_policy,
            flush_batch_size=config.flush_batch_size,
            flush_concurrency=config.flush_concurrency,
            max_connections=config.max_connections,
            max_keepalive_connections=config.max_keepalive_connections,
            enable_dlq=config.enable_dlq,
            circuit_failure_threshold=config.circuit_failure_threshold,
            circuit_recovery_timeout_s=config.circuit_recovery_timeout_s,
            dlq_spool_dir=config.dlq_spool_dir,
            dlq_best_effort_topics=config.dlq_best_effort_topics,
            keepalive_expiry_s=config.keepalive_expiry_s,
        )
        self._consumer = EventMeshConsumer(
            messaging_url=config.messaging_url,
            names=self._names,
            tokens=self._tokens,
            admin=self._admin,
            clients=self._clients,
            poll_interval_s=config.poll_interval_s,
            lock_duration_ms=config.lock_duration_ms,
            handler_timeout_s=config.handler_timeout_s,
            consume_transport=config.consume_transport,
            amqp_host=config.amqp_host,
            amqp_port=config.amqp_port,
            amqp_ws_path=config.amqp_ws_path,
            amqp_prefetch=config.amqp_prefetch,
            amqp_concurrency=config.amqp_concurrency,
            amqp_strip_namespace=config.amqp_strip_namespace,
            metrics=self._metrics,
            cooldown_scope=config.consume_429_scope,
            batch_max=config.consume_batch_max,
            runtime_fallback=config.runtime_fallback,
        )

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main asyncio event loop for dispatching async handlers."""
        self._consumer.set_event_loop(loop)

    # Sync/async HTTP pools live on the shared ``EventMeshHttpClients`` holder;
    # these proxies keep ``self._http`` / ``self._http_async`` working (and let
    # tests swap the pool for a mock that every component then sees).
    @property
    def _http(self) -> httpx.Client:
        return self._clients.sync

    @_http.setter
    def _http(self, value: httpx.Client) -> None:
        self._clients.sync = value

    @property
    def _http_async(self) -> httpx.AsyncClient | None:
        return self._clients.async_

    @_http_async.setter
    def _http_async(self, value: httpx.AsyncClient | None) -> None:
        self._clients.async_ = value

    # ------------------------------------------------------------------
    # Publish (→ EventMeshPublisher)
    # ------------------------------------------------------------------

    def publish_to_topic(
        self, topic: str, message: Any, key: Optional[str] = None
    ) -> None:
        self._publisher.publish_topic(topic, message, key)

    def publish_to_queue(self, queue_name: str, message: Any) -> None:
        self._publisher.publish_queue(queue_name, message)

    async def start_async(self) -> None:
        """Start the async send-buffer flusher + proactive token refresher."""
        await self._publisher.start()

    async def stop_async(self, drain_timeout_s: float = 10.0) -> None:
        """Stop the async flusher, draining in-flight messages up to the timeout."""
        await self._publisher.stop(drain_timeout_s=drain_timeout_s)

    def health_check(self) -> dict:
        """Snapshot of publisher + consumer state (does not hit Event Mesh)."""
        return {
            **self._publisher.health_check(),
            "consume": self._consumer.transport_state(),
        }

    def consume_transport_state(self) -> dict:
        """Consumer transport snapshot for the readiness gate (SA-1470)."""
        return self._consumer.transport_state()

    # ------------------------------------------------------------------
    # Consume (→ EventMeshConsumer)
    # ------------------------------------------------------------------

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        self._consumer.subscribe(topic, callback)

    def consume_from_queue(self, queue_name: str) -> Optional[dict]:
        return self._consumer.consume_from_queue(queue_name)

    def acknowledge(self, queue_name: str, message_id: str) -> None:
        self._consumer.acknowledge(queue_name, message_id)

    # ------------------------------------------------------------------
    # Management API (→ EventMeshAdmin) — parity with KafkaBrokerClient
    # ------------------------------------------------------------------

    def ensure_topic(self, topic: str, **_: Any) -> None:
        """Pre-create the queue + subscription pair for *topic*."""
        self._admin.ensure_topic(topic)

    def delete_topic_queue(self, topic: str) -> None:
        """Delete the queue provisioned for *topic* — the inverse of
        ``ensure_topic`` (SA-2087 R12).

        Takes a TOPIC, not a queue name, so callers never hand-build
        ``sub-<topic-with-dots-hyphenated>``: the local-name derivation lives
        here with the namespace that owns it (``EventMeshNames``), exactly as
        it does on the provisioning side.
        """
        self._admin.delete_queue(self._admin.queue_local_for(topic))

    def queue_exists(self, queue_name: str) -> bool:
        return self._admin.queue_exists(queue_name)

    def create_queue(self, queue_name: str, *, _allow_namespace_heal: bool = True) -> None:
        self._admin.create_queue(queue_name, _allow_namespace_heal=_allow_namespace_heal)

    def delete_queue(self, queue_name: str) -> None:
        self._admin.delete_queue(queue_name)

    def list_queues(self) -> list[str]:
        return self._admin.list_queues()

    def get_queue(self, queue_name: str) -> Optional[dict]:
        return self._admin.get_queue(queue_name)

    def consumer_count(self, queue_name: str) -> Optional[int]:
        return self._admin.consumer_count(queue_name)

    def queue_depth(self, queue_name: str) -> Optional[int]:
        return self._admin.queue_depth(queue_name)

    @property
    def namespace(self) -> str:
        """The (possibly self-healed) broker namespace these names qualify against."""
        return self._names.namespace

    def subscription_exists(self, queue_name: str, topic: str) -> bool:
        return self._admin.subscription_exists(queue_name, topic)

    def create_subscription(self, queue_name: str, topic: str) -> None:
        self._admin.create_subscription(queue_name, topic)

    def delete_subscription(self, queue_name: str, topic: str) -> None:
        self._admin.delete_subscription(queue_name, topic)

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the consumer + publisher and close the sync HTTP client.

        Note: for async shutdown with in-flight message draining, callers
        should ``await stop_async()`` FIRST, then ``close()``.
        """
        self._consumer.close()
        self._publisher.close()
        try:
            self._clients.sync.close()
        except Exception:
            logger.exception("Error closing Event Mesh HTTP client")
