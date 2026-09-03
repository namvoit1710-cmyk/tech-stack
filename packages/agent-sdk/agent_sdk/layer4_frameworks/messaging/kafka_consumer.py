from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Awaitable, Callable

from agent_sdk.layer4_frameworks.messaging.topic_utils import (
    has_static_callable_attr,
    normalize_topics,
)

_TOPIC_ENSURE_MAX_ATTEMPTS = 3
_TOPIC_ENSURE_INITIAL_BACKOFF_SECONDS = 1.0


class KafkaMessageDelivery:
    def __init__(
        self, *, broker_client: Any, message: Any, payload: dict[str, Any]
    ) -> None:
        self._broker = broker_client
        self._message = message
        self.payload = payload
        self._resolved = False

    async def ack(self) -> None:
        if self._resolved:
            return
        commit = getattr(self._broker, "commit_message", None)
        if commit is not None:
            commit(self._message, asynchronous=False)
        self._resolved = True

    async def nack(self, requeue: bool = True) -> None:
        if self._resolved:
            return
        nack = getattr(self._broker, "nack_message", None)
        if nack is not None:
            nack(self._message, requeue=requeue)
        self._resolved = True

    async def reject(self) -> None:
        if self._resolved:
            return
        reject = getattr(self._broker, "reject_message", None)
        if reject is not None:
            reject(self._message)
        self._resolved = True


class KafkaMessageConsumer:
    requires_shutdown_wait = True

    def __init__(
        self,
        broker_client: Any,
        topic: str | None = None,
        logger: Any = None,
        topics: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._broker = broker_client
        self._topics = normalize_topics(topic=topic, topics=topics)
        if not self._topics:
            raise ValueError("KafkaMessageConsumer requires at least one topic")
        self._topic = self._topics[0]  # backward-compatible attribute
        self._logger = logger
        self._running = False

    async def start(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._running = True

        async def _dispatch(raw: Any) -> None:
            payload = raw
            message_topic = self._resolve_message_topic(raw)
            if hasattr(raw, "value") and callable(raw.value):
                try:
                    payload = json.loads(raw.value().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    self._logger.error(
                        "Failed to decode Kafka message", topic=message_topic
                    )
                    reject = getattr(self._broker, "reject_message", None)
                    if reject is not None:
                        reject(raw, reason="decode_error")
                    return
            correlation_id = (
                payload.get("correlation_id", "") if isinstance(payload, dict) else ""
            )
            self._logger.info(
                "Message received", topic=message_topic, correlation_id=correlation_id
            )
            try:
                delivery = KafkaMessageDelivery(
                    broker_client=self._broker,
                    message=raw,
                    payload=payload,
                )
                await handler(delivery)
            except Exception as exc:
                self._logger.error(
                    "Handler error",
                    topic=message_topic,
                    correlation_id=correlation_id,
                    error=str(exc),
                )

        try:
            await self._ensure_topics_exist_before_subscribe()
            if has_static_callable_attr(self._broker, "subscribe_to_topics"):
                await asyncio.to_thread(
                    self._broker.subscribe_to_topics, self._topics, _dispatch
                )
            else:
                for topic in self._topics:
                    await asyncio.to_thread(
                        self._broker.subscribe_to_topic, topic, _dispatch
                    )
            self._logger.info("KafkaMessageConsumer started", topics=self._topics)
        except Exception:
            self._running = False
            raise

    def cancel_conversation(self, conv_id: str) -> int:
        """SA-892: delegate a cooperative cancel to the broker's dispatch runtime."""
        cancel = getattr(self._broker, "cancel_conversation", None)
        if callable(cancel):
            return cancel(conv_id)
        return 0

    @staticmethod
    def _resolve_message_topic(raw: Any) -> str:
        topic = getattr(raw, "topic", None)
        if callable(topic):
            try:
                return str(topic())
            except Exception:
                return ""
        return str(topic or "")

    async def _ensure_topics_exist_before_subscribe(self) -> None:
        ensure_topic_exists = getattr(self._broker, "ensure_topic_exists", None)
        if not callable(ensure_topic_exists):
            return

        await asyncio.gather(
            *(
                self._ensure_topic_exists_before_subscribe(topic)
                for topic in self._topics
            )
        )

    async def _ensure_topic_exists_before_subscribe(self, topic: str) -> None:
        ensure_topic_exists = getattr(self._broker, "ensure_topic_exists", None)
        if not callable(ensure_topic_exists):
            return

        delay_seconds = _TOPIC_ENSURE_INITIAL_BACKOFF_SECONDS
        for attempt in range(1, _TOPIC_ENSURE_MAX_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(ensure_topic_exists, topic)
                return
            except Exception as exc:
                if attempt == _TOPIC_ENSURE_MAX_ATTEMPTS:
                    self._logger.warning(
                        "Kafka topic auto-create failed; continuing to subscribe",
                        topic=topic,
                        attempts=attempt,
                        error=str(exc),
                    )
                    return

                self._logger.warning(
                    "Kafka topic auto-create failed; retrying",
                    topic=topic,
                    attempt=attempt,
                    max_attempts=_TOPIC_ENSURE_MAX_ATTEMPTS,
                    retry_in_seconds=delay_seconds,
                    error=str(exc),
                )
                await asyncio.sleep(delay_seconds)
                delay_seconds *= 2

    async def stop(self) -> None:
        self._running = False
        close = getattr(self._broker, "close", None)
        if callable(close):
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
        self._logger.info("KafkaMessageConsumer stopped", topics=self._topics)
