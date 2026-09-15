"""Kafka implementation of IMessageBrokerClient.

Wraps ``confluent_kafka.Producer`` and ``Consumer`` to satisfy the full
IMessageBrokerClient protocol used by the worker-executor-service.

Kafka does not have native queue semantics. Queue-related methods are
mapped to Kafka topic primitives:
- ``publish_to_queue`` → ``produce`` to a topic named after the queue
- ``consume_from_queue`` → single ``poll`` on a consumer subscribed to
  the queue-named topic
- ``acknowledge`` → no-op (auto-commit enabled)
- Queue/subscription management → no-ops (Kafka topics are auto-created)
- ``publish_to_dlq`` → produce to ``{topic}.dlq``
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Optional
from collections.abc import Callable

from confluent_kafka import Consumer, KafkaError, Producer

from app.layer4_frameworks.messaging.kafka_memory_budget import apply_buffer_bounds

from app.layer1_domain.errors import MessageTooLargeError

from app.layer2_application.interfaces.message_broker_interface import IMessageBrokerClient

logger = logging.getLogger(__name__)

# SA-1941 — WARN (don't reject) when a broker message is large; it is almost
# always a large inline worker output that should instead be relayed as a
# pointer via worker-side streaming (SA-1944). ~Kafka's default max message size.
_MAX_BROKER_MESSAGE_BYTES = 1024 * 1024  # 1 MiB


class KafkaBrokerClient(IMessageBrokerClient):
    """Concrete message broker client backed by confluent-kafka.

    Provides both topic-based publish/subscribe and queue-style
    consume/acknowledge needed by the worker execution adapters.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str = "ai-workflow",
        auto_offset_reset: str = "earliest",
        **extra_config: Any,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset

        # -- Producer (created eagerly) --
        # SA-2266 — librdkafka's produce backlog defaults to 1 GiB, a C-side
        # allocation invisible to every Python-level metric. Bounds are applied
        # UNDER extra_config so a caller-supplied value still wins.
        producer_conf: dict[str, Any] = apply_buffer_bounds(
            {
                "bootstrap.servers": bootstrap_servers,
                **extra_config,
            },
            role="producer",
        )
        self._producer = Producer(producer_conf)

        # -- Shared consumer config --
        self._consumer_conf: dict[str, Any] = apply_buffer_bounds(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": auto_offset_reset,
                "enable.auto.commit": True,
                **extra_config,
            },
            role="consumer",
        )

        # -- Subscribe-based consumer (for subscribe_to_topic) --
        self._sub_consumer: Consumer | None = None
        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._consumer_thread: threading.Thread | None = None
        self._running = False
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # -- Queue consumer (for consume_from_queue) --
        self._queue_consumer: Consumer | None = None
        self._queue_topics: set[str] = set()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main asyncio event loop for dispatching async handlers."""
        self._main_loop = loop

    # ------------------------------------------------------------------
    # publish_to_topic
    # ------------------------------------------------------------------

    def publish_to_topic(
        self, topic: str, message: Any, key: Optional[str] = None
    ) -> None:
        """Serialize *message* as JSON and produce to *topic*."""
        payload = json.dumps(message).encode("utf-8")
        if len(payload) > _MAX_BROKER_MESSAGE_BYTES:
            # SA-1951 — WARN-then-produce let an oversized result reach the
            # producer, which rejects it; the caller lost the task result. Raise
            # typed so the caller can degrade to a small message.
            raise MessageTooLargeError(
                f"topic {topic}", len(payload), _MAX_BROKER_MESSAGE_BYTES,
            )
        encoded_key = key.encode("utf-8") if key else None

        try:
            self._producer.produce(
                topic,
                value=payload,
                key=encoded_key,
                callback=self._delivery_callback,
            )
            self._producer.poll(0)
        except BufferError:
            logger.warning(
                "Producer buffer full — flushing before retry (topic=%s)", topic
            )
            self._producer.flush(timeout=5)
            self._producer.produce(
                topic,
                value=payload,
                key=encoded_key,
                callback=self._delivery_callback,
            )
            self._producer.poll(0)

    @staticmethod
    def _delivery_callback(err: Any, msg: Any) -> None:
        if err is not None:
            logger.error(
                "Message delivery failed: %s (topic=%s)", err, msg.topic()
            )
        else:
            logger.debug(
                "Message delivered to %s [%s]", msg.topic(), msg.partition()
            )

    # ------------------------------------------------------------------
    # subscribe_to_topic (async callback-based)
    # ------------------------------------------------------------------

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        """Register *callback* for *topic* and start consuming."""
        with self._lock:
            self._callbacks.setdefault(topic, []).append(callback)
            topics = list(self._callbacks.keys())

        if self._sub_consumer is None:
            self._sub_consumer = Consumer(self._consumer_conf)

        self._sub_consumer.subscribe(topics)
        logger.info("Subscribed to topics: %s", topics)

        if self._consumer_thread is None:
            self._running = True
            self._consumer_thread = threading.Thread(
                target=self._consume_loop,
                name="kafka-consumer-loop",
                daemon=True,
            )
            self._consumer_thread.start()

    def _consume_loop(self) -> None:
        """Background thread: poll Kafka and dispatch to callbacks."""
        try:
            while self._running:
                self._producer.poll(0)

                msg = self._sub_consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Consumer error: %s", msg.error())
                    continue

                topic = msg.topic()
                try:
                    payload = json.loads(msg.value().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.error(
                        "Failed to deserialize message on %s: %s", topic, exc
                    )
                    continue

                with self._lock:
                    handlers = list(self._callbacks.get(topic, []))

                for handler in handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            if self._main_loop is not None and not self._main_loop.is_closed():
                                asyncio.run_coroutine_threadsafe(
                                    handler(payload), self._main_loop
                                )
                            else:
                                logger.warning(
                                    "No main event loop — dropping async handler for topic %s", topic
                                )
                        else:
                            handler(payload)
                    except Exception:
                        logger.exception(
                            "Error in callback for topic %s", topic
                        )
        except Exception:
            logger.exception("Consumer loop crashed")

    # ------------------------------------------------------------------
    # Queue-based methods (mapped to Kafka topics)
    # ------------------------------------------------------------------

    def publish_to_queue(self, queue_name: str, message: Any) -> None:
        """Produce to a topic named *queue_name*."""
        self.publish_to_topic(queue_name, message)

    def consume_from_queue(self, queue_name: str) -> Optional[dict]:
        """Single-poll consume from a topic named *queue_name*.

        Returns ``{"data": <deserialized>, "message_id": <offset>}`` or
        ``None`` if no message is available within the timeout.
        """
        if self._queue_consumer is None:
            conf = {**self._consumer_conf, "group.id": f"{self._group_id}-queue"}
            self._queue_consumer = Consumer(conf)

        if queue_name not in self._queue_topics:
            self._queue_topics.add(queue_name)
            self._queue_consumer.subscribe(list(self._queue_topics))

        msg = self._queue_consumer.poll(timeout=1.0)
        if msg is None:
            return None
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            logger.error("Queue consumer error: %s", msg.error())
            return None

        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error(
                "Failed to deserialize queue message on %s: %s",
                queue_name, exc,
            )
            return None

        return {
            "data": payload,
            "message_id": f"{msg.partition()}:{msg.offset()}",
        }

    def acknowledge(self, queue_name: str, message_id: str) -> None:
        """No-op — Kafka auto-commits offsets."""
        logger.debug(
            "Acknowledge (no-op): queue=%s message_id=%s",
            queue_name, message_id,
        )

    # ------------------------------------------------------------------
    # Queue / subscription management (no-ops for Kafka)
    # ------------------------------------------------------------------

    def queue_exists(self, queue_name: str) -> bool:
        """Kafka topics are auto-created; always returns True."""
        return True

    def create_queue(self, queue_name: str) -> None:
        """No-op — Kafka topics are auto-created on first produce."""
        logger.debug("create_queue (no-op): %s", queue_name)

    def delete_queue(self, queue_name: str) -> None:
        """No-op — Kafka topic deletion not supported via client."""
        logger.debug("delete_queue (no-op): %s", queue_name)

    def subscription_exists(self, queue_name: str, topic: str) -> bool:
        """Always returns True for Kafka."""
        return True

    def create_subscription(self, queue_name: str, topic: str) -> None:
        """No-op — Kafka subscriptions are handled by consumer groups."""
        logger.debug("create_subscription (no-op): queue=%s topic=%s", queue_name, topic)

    def delete_subscription(self, queue_name: str, topic: str) -> None:
        """No-op."""
        logger.debug("delete_subscription (no-op): queue=%s topic=%s", queue_name, topic)

    # ------------------------------------------------------------------
    # Dead-letter queue
    # ------------------------------------------------------------------

    def publish_to_dlq(self, original_topic: str, message: Any, error: str) -> None:
        """Publish failed message to ``{original_topic}.dlq``."""
        dlq_topic = f"{original_topic}.dlq"
        envelope = {
            "original_topic": original_topic,
            "error": error,
            "message": message,
        }
        self.publish_to_topic(dlq_topic, envelope)

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop consumer thread, close consumers and flush producer."""
        self._running = False

        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=10)
            self._consumer_thread = None

        if self._sub_consumer is not None:
            try:
                self._sub_consumer.close()
            except Exception:
                logger.exception("Error closing subscribe consumer")
            self._sub_consumer = None

        if self._queue_consumer is not None:
            try:
                self._queue_consumer.close()
            except Exception:
                logger.exception("Error closing queue consumer")
            self._queue_consumer = None

        try:
            self._producer.flush(timeout=10)
        except Exception:
            logger.exception("Error flushing Kafka producer")
