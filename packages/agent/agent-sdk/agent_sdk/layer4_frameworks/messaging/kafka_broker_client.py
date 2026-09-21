from __future__ import annotations

import asyncio
import importlib
import json
import logging
import queue
import threading
from typing import Any, Callable

from agent_sdk.layer1_domain.value_objects.agent_control import (
    extract_conv_id,
    is_agent_control_message,
)
from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
    ConsumerDispatchRuntime,
)

logger = logging.getLogger(__name__)


def _extract_kafka_error_code(exc: Exception) -> int | None:
    candidates = []
    if getattr(exc, "args", None):
        candidates.append(exc.args[0])
    candidates.append(exc)

    for candidate in candidates:
        code = getattr(candidate, "code", None)
        if callable(code):
            try:
                return int(code())
            except (TypeError, ValueError):
                return None
    return None


def _is_topic_already_exists_error(exc: Exception) -> bool:
    kafka_error_type: Any | None = None
    try:
        confluent_kafka: Any = importlib.import_module("confluent_kafka")
        kafka_error_type = getattr(confluent_kafka, "KafkaError", None)
    except Exception:
        kafka_error_type = None

    error_code = _extract_kafka_error_code(exc)
    if kafka_error_type is not None and error_code == getattr(
        kafka_error_type, "TOPIC_ALREADY_EXISTS", None
    ):
        return True

    return "TOPIC_ALREADY_EXISTS" in str(exc).strip().upper()


class KafkaBrokerClient:
    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str = "agent-sdk-consumer",
        auto_offset_reset: str = "earliest",
        max_in_flight_messages: int = 4,
        shutdown_grace_seconds: int = 30,
        **extra_config: Any,
    ) -> None:
        from confluent_kafka import Producer

        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        broker_config = dict(extra_config)
        self._kafka_reject_topic = broker_config.pop("kafka_reject_topic", None)

        producer_conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap_servers,
            **broker_config,
        }
        self._producer = Producer(producer_conf)

        self._consumer = None
        self._consumer_conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": False,
            **broker_config,
        }

        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._consumer_thread: threading.Thread | None = None
        self._consumer_command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_commit_offset: dict[tuple[str, int], int] = {}
        self._inflight_offsets: dict[tuple[str, int], set[int]] = {}
        self._resolved_messages: dict[tuple[str, int], dict[int, Any]] = {}
        self._running = False
        self._dispatch_runtime = ConsumerDispatchRuntime(
            max_in_flight=max_in_flight_messages,
            drain_timeout_seconds=shutdown_grace_seconds,
            logger=logger,
        )
        self._admin_client: Any | None = None
        self._admin_lock = threading.Lock()

    def publish_to_topic(
        self, topic: str, message: Any, key: str | None = None
    ) -> None:
        payload = json.dumps(message).encode("utf-8")
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
    def _delivery_callback(err, msg) -> None:
        if err is not None:
            logger.error("Message delivery failed: %s (topic=%s)", err, msg.topic())
        else:
            logger.debug("Message delivered to %s [%s]", msg.topic(), msg.partition())

    def _get_admin_client(self) -> Any:
        if self._admin_client is None:
            from confluent_kafka.admin import AdminClient

            self._admin_client = AdminClient(
                {"bootstrap.servers": self._bootstrap_servers}
            )
        return self._admin_client

    def ensure_topic_exists(
        self,
        topic: str,
        *,
        num_partitions: int = 1,
        replication_factor: int = 1,
    ) -> None:
        from confluent_kafka.admin import NewTopic

        with self._admin_lock:
            admin_client = self._get_admin_client()
            metadata = admin_client.list_topics(timeout=5)
            topic_metadata = getattr(metadata, "topics", {}).get(topic)
            if topic_metadata is not None:
                metadata_error = getattr(topic_metadata, "error", None)
                if metadata_error is not None:
                    logger.info(
                        "Kafka topic present with transient metadata error; "
                        "skipping creation: %s (%s)",
                        topic,
                        metadata_error,
                    )
                return

            futures = admin_client.create_topics(
                [
                    NewTopic(
                        topic=topic,
                        num_partitions=num_partitions,
                        replication_factor=replication_factor,
                    )
                ]
            )
            creation = futures.get(topic)
            if creation is None:
                raise RuntimeError(
                    f"Kafka admin did not return a future for topic: {topic}"
                )
            try:
                creation.result()
            except Exception as exc:
                if _is_topic_already_exists_error(exc):
                    logger.info("Kafka topic already exists: %s", topic)
                    return
                raise

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        self.subscribe_to_topics([topic], callback)

    def subscribe_to_topics(
        self, topics: list[str] | tuple[str, ...], callback: Callable
    ) -> None:
        from confluent_kafka import Consumer

        normalized_topics = list(
            dict.fromkeys(str(item).strip() for item in topics if str(item).strip())
        )
        if not normalized_topics:
            return

        with self._lock:
            for topic in normalized_topics:
                self._callbacks.setdefault(topic, []).append(callback)
            subscribed_topics = list(self._callbacks.keys())

            if self._consumer is None:
                self._consumer = Consumer(self._consumer_conf)

            self._consumer.subscribe(subscribed_topics)
            logger.info("Subscribed to topics: %s", subscribed_topics)

            if self._consumer_thread is None:
                self._running = True
                self._consumer_thread = threading.Thread(
                    target=self._consume_loop,
                    name="kafka-consumer-loop",
                    daemon=True,
                )
                self._consumer_thread.start()

    def commit_message(self, message: Any, asynchronous: bool = False) -> None:
        if self._consumer is None:
            return
        self._enqueue_consumer_command(
            action="ack",
            message=message,
            asynchronous=asynchronous,
        )

    def nack_message(self, message: Any, requeue: bool = True) -> None:
        if self._consumer is None:
            return

        if requeue:
            self._enqueue_consumer_command(
                action="seek",
                message=message,
            )
            return

        logger.info(
            "Kafka delivery left unresolved without requeue",
            extra={
                "requeue": False,
                "topic": getattr(message, "topic", lambda: None)(),
            },
        )

    def reject_message(self, message: Any, reason: str | None = None) -> None:
        if self._consumer is None:
            return
        self._enqueue_consumer_command(
            action="reject",
            message=message,
            asynchronous=False,
            reason=reason,
        )

    def _build_reject_headers(
        self, *, message: Any, reason: str | None = None
    ) -> list[tuple[str, str | bytes | None]]:
        headers: list[tuple[str, str | bytes | None]] = []
        raw_headers = (
            message.headers()
            if hasattr(message, "headers") and callable(message.headers)
            else None
        )
        if raw_headers:
            for key, value in raw_headers:
                if value is None:
                    continue
                headers.append(
                    (
                        str(key),
                        value.decode("utf-8")
                        if isinstance(value, bytes)
                        else str(value),
                    )
                )
        headers.append(("x-original-topic", str(message.topic() or "")))
        if reason:
            headers.append(("x-reject-reason", reason))
        return headers

    @staticmethod
    def _classify_control(msg: Any) -> tuple[bool, str]:
        """Peek a Kafka message to decide control vs normal + its conv_id.

        Decoding failures degrade gracefully to a normal (non-control) message
        with no conv_id, so a malformed payload is still dispatched normally and
        settled by the existing handler path.
        """
        try:
            value = msg.value()
        except (AttributeError, TypeError):
            return False, ""
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return False, ""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return False, ""
        if not isinstance(value, dict):
            return False, ""
        if is_agent_control_message(value):
            return True, ""
        return False, extract_conv_id(value)

    def cancel_conversation(self, conv_id: str) -> int:
        """SA-892: cooperatively cancel a conversation's in-flight turn(s)."""
        return self._dispatch_runtime.cancel(conv_id)

    async def _run_handler(self, handler: Callable, msg: Any, topic: str) -> None:
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(msg)
            else:
                handler(msg)
        except Exception:
            logger.exception("Error in callback for topic %s", topic)

    def _consume_loop(self) -> None:
        from confluent_kafka import KafkaError

        consumer = self._consumer
        if consumer is None:
            return

        try:
            while self._running:
                self._producer.poll(0)
                self._drain_consumer_commands()
                msg = consumer.poll(timeout=1.0)
                self._drain_consumer_commands()
                if msg is None:
                    continue
                error = msg.error()
                if error is not None:
                    if (
                        KafkaError is not None
                        and error.code() == KafkaError._PARTITION_EOF
                    ):
                        continue
                    logger.error("Consumer error: %s", error)
                    continue

                topic = str(msg.topic() or "")
                if not topic:
                    continue
                self._track_inflight_message(msg)

                with self._lock:
                    handlers = list(self._callbacks.get(topic, []))

                control, conv_id = self._classify_control(msg)
                for handler in handlers:
                    try:
                        run_coro = self._run_handler(handler, msg, topic)
                        # SA-892 (N4): control (stop) bypasses the in-flight
                        # semaphore so it is not starved behind a running turn
                        # under max_in_flight=1.
                        if control:
                            self._dispatch_runtime.submit_control(run_coro)
                        else:
                            self._dispatch_runtime.submit(run_coro, conv_id=conv_id)
                    except RuntimeError:
                        if not self._running:
                            return
                        logger.exception(
                            "Dispatch runtime rejected callback for topic %s", topic
                        )
        except Exception:
            logger.exception("Consumer loop crashed")

    def _enqueue_consumer_command(
        self,
        *,
        action: str,
        message: Any,
        asynchronous: bool = False,
        reason: str | None = None,
    ) -> None:
        if not hasattr(self, "_consumer_command_queue"):
            self._consumer_command_queue = queue.Queue()
        self._consumer_command_queue.put(
            {
                "action": action,
                "message": message,
                "asynchronous": asynchronous,
                "reason": reason,
                "topic": message.topic(),
                "partition": message.partition(),
                "offset": message.offset(),
            }
        )

    def _track_inflight_message(self, message: Any) -> None:
        key = (message.topic(), message.partition())
        offset = message.offset()
        inflight = self._inflight_offsets.setdefault(key, set())
        inflight.add(offset)
        next_offset = self._next_commit_offset.get(key)
        if next_offset is None or offset < next_offset:
            self._next_commit_offset[key] = offset
        self._resolved_messages.setdefault(key, {})

    def _drain_consumer_commands(self) -> None:
        consumer = self._consumer
        if consumer is None or not hasattr(self, "_consumer_command_queue"):
            return

        while True:
            try:
                command = self._consumer_command_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_consumer_command(command, consumer)

    def _handle_consumer_command(self, command: dict[str, Any], consumer: Any) -> None:
        action = str(command.get("action") or "")
        topic = str(command.get("topic") or "")
        partition = int(command.get("partition") or 0)
        offset = int(command.get("offset") or 0)
        key = (topic, partition)
        message = command.get("message")

        if action == "seek":
            from confluent_kafka import TopicPartition

            self._reset_partition_state(key, offset)
            consumer.seek(TopicPartition(topic, partition, offset))
            logger.info(
                "Kafka delivery rewound for retry",
                extra={
                    "requeue": True,
                    "topic": topic,
                    "partition": partition,
                    "offset": offset,
                },
            )
            return

        inflight = self._inflight_offsets.get(key)
        if not inflight or offset not in inflight:
            return

        if action == "reject":
            self._publish_rejected_message(
                message=message,
                reason=command.get("reason"),
            )

        highest_message = self._mark_offset_resolved(key, offset, message)
        if highest_message is None:
            return

        consumer.commit(
            message=highest_message,
            asynchronous=bool(command.get("asynchronous", False)),
        )

    def _mark_offset_resolved(
        self,
        key: tuple[str, int],
        offset: int,
        message: Any,
    ) -> Any | None:
        inflight = self._inflight_offsets.get(key)
        if inflight is None or offset not in inflight:
            return None

        resolved = self._resolved_messages.setdefault(key, {})
        resolved[offset] = message
        next_offset = self._next_commit_offset.get(key, offset)
        highest_message = None

        while next_offset in resolved:
            highest_message = resolved.pop(next_offset)
            inflight.discard(next_offset)
            next_offset += 1

        if inflight or resolved:
            self._next_commit_offset[key] = next_offset
        else:
            self._next_commit_offset.pop(key, None)
            self._inflight_offsets.pop(key, None)
            self._resolved_messages.pop(key, None)

        return highest_message

    def _reset_partition_state(self, key: tuple[str, int], offset: int) -> None:
        self._next_commit_offset[key] = offset
        self._inflight_offsets[key] = set()
        self._resolved_messages[key] = {}

    def _publish_rejected_message(
        self,
        *,
        message: Any,
        reason: str | None,
    ) -> None:
        reject_topic = getattr(self, "_kafka_reject_topic", None)
        if not reject_topic:
            return
        headers = self._build_reject_headers(message=message, reason=reason)
        self._producer.produce(
            reject_topic,
            value=message.value(),
            key=message.key()
            if hasattr(message, "key") and callable(message.key)
            else None,
            headers=headers,
            callback=self._delivery_callback,
        )
        self._producer.poll(0)

    def flush_producer(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout=timeout)
        if remaining:
            raise RuntimeError(
                f"Kafka producer still has {remaining} message(s) after flush"
            )

    def close(self) -> None:
        self._running = False
        self._dispatch_runtime.close_for_new_work()
        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=10)
            self._consumer_thread = None
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                logger.exception("Error closing Kafka consumer")
            self._consumer = None
        self._dispatch_runtime.drain_and_close()
        with self._admin_lock:
            if self._admin_client is not None:
                try:
                    close = getattr(self._admin_client, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    logger.exception("Error closing Kafka admin client")
                finally:
                    self._admin_client = None
        try:
            self._producer.flush(timeout=10)
        except Exception:
            logger.exception("Error flushing Kafka producer")
