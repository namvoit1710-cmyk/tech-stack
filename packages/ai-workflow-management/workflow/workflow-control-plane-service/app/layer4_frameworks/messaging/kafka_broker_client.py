"""Kafka implementation of the message broker client.

Wraps ``confluent_kafka.Producer`` and ``Consumer`` to provide
publish/subscribe semantics for the workflow control plane service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from collections.abc import Callable

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from app.layer4_frameworks.messaging.kafka_memory_budget import apply_buffer_bounds

from app.layer2_application.interfaces.broker_client_interface import IBrokerClient

logger = logging.getLogger(__name__)


class KafkaBrokerClient(IBrokerClient):
    """Concrete message broker client backed by confluent-kafka.

    Provides ``publish_to_topic`` and ``subscribe_to_topic`` used by
    the control plane's Kafka adapters (event publisher, callback publisher).
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str = "ai-workflow",
        auto_offset_reset: str = "earliest",
        handler_timeout_s: float | None = None,
        **extra_config: Any,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        # Per-message handler timeout for async callbacks dispatched to
        # ``self._main_loop``. ``None`` blocks indefinitely (preserves pre-3.0
        # behaviour for long-running handlers); Executor/ExecutorConsumer
        # callers should set a bounded value.
        self._handler_timeout_s = handler_timeout_s

        # -- Producer (created eagerly) --
        # SA-2266 — librdkafka's produce backlog defaults to 1 GiB
        # (queue.buffering.max.kbytes), a C-side allocation invisible to every
        # Python-level metric and four times the whole 256MB pod. Bounds are
        # applied UNDER extra_config, so any caller-supplied value still wins.
        producer_conf: dict[str, Any] = apply_buffer_bounds(
            {
                "bootstrap.servers": bootstrap_servers,
                **extra_config,
            },
            role="producer",
        )
        self._producer = Producer(producer_conf)

        # -- Consumer (created lazily on first subscribe) --
        # At-least-once semantics: auto-commit disabled so offsets only
        # advance after all handlers for a message return successfully.
        # Callback exception → no commit → Kafka redelivers after the
        # consumer-group session timeout or next rebalance.
        self._consumer: Consumer | None = None
        self._consumer_conf: dict[str, Any] = apply_buffer_bounds(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": auto_offset_reset,
                "enable.auto.commit": False,
                **extra_config,
            },
            role="consumer",
        )

        # -- Subscription bookkeeping --
        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._consumer_thread: threading.Thread | None = None
        self._running = False
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main asyncio event loop for dispatching async handlers."""
        self._main_loop = loop

    # ------------------------------------------------------------------
    # publish_to_topic
    # ------------------------------------------------------------------

    def publish_to_topic(
        self, topic: str, message: Any, key: str | None = None
    ) -> None:
        """Serialize *message* as JSON and produce to *topic*."""
        payload = json.dumps(message).encode("utf-8")
        encoded_key = key.encode("utf-8") if key else None

        try:
            # print(json.dumps(payload))
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
    # Topic management
    # ------------------------------------------------------------------

    def ensure_topic(
        self,
        topic: str,
        num_partitions: int = 1,
        replication_factor: int = 1,
        timeout: float = 10.0,
    ) -> None:
        """Create the topic if it does not already exist."""
        from confluent_kafka.admin import AdminClient, NewTopic  # noqa: F811 — lazy import

        admin = AdminClient({"bootstrap.servers": self._bootstrap_servers})
        try:
            metadata = admin.list_topics(timeout=timeout)
            if topic in metadata.topics:
                logger.debug("Topic '%s' already exists", topic)
                return
        except Exception:
            logger.warning("Could not list topics — will attempt creation anyway")

        new_topic = NewTopic(
            topic,
            num_partitions=num_partitions,
            replication_factor=replication_factor,
        )
        futures = admin.create_topics([new_topic])
        for t, future in futures.items():
            try:
                future.result(timeout=timeout)
                logger.info("Created Kafka topic '%s'", t)
            except KafkaException as exc:
                # Topic already exists is not an error
                if "TOPIC_ALREADY_EXISTS" in str(exc):
                    logger.debug("Topic '%s' already exists", t)
                else:
                    logger.error("Failed to create topic '%s': %s", t, exc)
                    raise
            except Exception as exc:
                if "TOPIC_ALREADY_EXISTS" in str(exc):
                    logger.debug("Topic '%s' already exists", t)
                else:
                    logger.error("Failed to create topic '%s': %s", t, exc)
                    raise

    # ------------------------------------------------------------------
    # subscribe_to_topic
    # ------------------------------------------------------------------

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        """Register *callback* for *topic* and start consuming."""
        with self._lock:
            self._callbacks.setdefault(topic, []).append(callback)
            topics = list(self._callbacks.keys())

        # Create consumer on first subscription
        if self._consumer is None:
            self._consumer = Consumer(self._consumer_conf)

        self._consumer.subscribe(topics)
        logger.info("Subscribed to topics: %s", topics)

        # Start the background poll loop once
        if self._consumer_thread is None:
            self._running = True
            self._consumer_thread = threading.Thread(
                target=self._consume_loop,
                name="kafka-consumer-loop",
                daemon=True,
            )
            self._consumer_thread.start()

    def _commit_safely(self, msg: Any, topic: str) -> None:
        """Synchronously commit the offset for *msg*, logging any failure.

        Errors here are not fatal — on the next poll the consumer either
        redelivers the same offset (safe, callbacks must be idempotent) or
        advances naturally. The commit is synchronous so the broker
        acknowledges before the poll loop moves on.
        """
        if self._consumer is None:
            return
        try:
            self._consumer.commit(message=msg, asynchronous=False)
        except Exception:
            logger.exception("Failed to commit offset for topic %s", topic)

    def _consume_loop(self) -> None:
        """Background thread: poll Kafka and dispatch to callbacks."""
        try:
            while self._running:
                # Service producer delivery callbacks
                self._producer.poll(0)

                msg = self._consumer.poll(timeout=1.0)
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
                    # Poison-pill: deserialize errors cannot be fixed by
                    # redelivery. Commit past the bad offset with a loud log
                    # so the partition is not blocked forever.
                    logger.error(
                        "Failed to deserialize message on %s: %s — committing past poison pill",
                        topic, exc,
                    )
                    self._commit_safely(msg, topic)
                    continue

                with self._lock:
                    handlers = list(self._callbacks.get(topic, []))

                all_ok = True
                for handler in handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            if self._main_loop is not None and not self._main_loop.is_closed():
                                fut = asyncio.run_coroutine_threadsafe(
                                    handler(payload), self._main_loop
                                )
                                # Block the poll thread until the coroutine
                                # finishes so the commit reflects handler
                                # outcome. This is a throughput/correctness
                                # tradeoff; bounded by ``handler_timeout_s``.
                                fut.result(timeout=self._handler_timeout_s)
                            else:
                                logger.warning(
                                    "No main event loop — dropping async handler for topic %s", topic
                                )
                                all_ok = False
                        else:
                            handler(payload)
                    except Exception:
                        logger.exception(
                            "Error in callback for topic %s", topic
                        )
                        all_ok = False

                if all_ok:
                    self._commit_safely(msg, topic)
                else:
                    logger.warning(
                        "Skipping commit for topic %s — handler failure; "
                        "message will redeliver on rebalance/restart",
                        topic,
                    )
        except Exception:
            logger.exception("Consumer loop crashed")

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop consumer thread, close consumer and flush producer."""
        self._running = False

        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=10)
            self._consumer_thread = None

        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                logger.exception("Error closing Kafka consumer")
            self._consumer = None

        try:
            self._producer.flush(timeout=10)
        except Exception:
            logger.exception("Error flushing Kafka producer")
