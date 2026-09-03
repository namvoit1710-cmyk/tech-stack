from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Awaitable, Callable

from agent_sdk.layer4_frameworks.messaging.topic_utils import (
    has_static_callable_attr,
    normalize_topics,
)


def _resolve_queue_name(broker_client: Any, topic: str) -> str:
    build_queue_name = getattr(broker_client, "build_queue_name", None)
    if callable(build_queue_name):
        return build_queue_name(topic)

    legacy_build_queue_name = getattr(broker_client, "_build_queue_name", None)
    if callable(legacy_build_queue_name):
        return legacy_build_queue_name(topic)

    return f"default/{topic}"


class EventMeshMessageDelivery:
    def __init__(
        self,
        *,
        broker_client: Any,
        queue_name: str,
        payload: dict[str, Any],
        message_headers: dict[str, str],
    ) -> None:
        self._broker = broker_client
        self._queue_name = queue_name
        self._headers = message_headers
        self.payload = payload
        self._resolved = False

    async def ack(self) -> None:
        if self._resolved:
            return
        ack = getattr(self._broker, "ack_message", None)
        if ack is not None:
            ack(self._queue_name, self._headers)
        self._resolved = True

    async def nack(self, requeue: bool = True) -> None:
        if self._resolved:
            return
        nack = getattr(self._broker, "nack_message", None)
        if nack is not None:
            nack(self._queue_name, self._headers, requeue=requeue)
        else:
            reject = getattr(self._broker, "reject_message", None)
            if reject is not None:
                reject(self._queue_name, self._headers, requeue=requeue)
        self._resolved = True

    async def reject(self) -> None:
        if self._resolved:
            return
        reject = getattr(self._broker, "reject_message", None)
        if reject is not None:
            reject(self._queue_name, self._headers, requeue=False)
        self._resolved = True


class EventMeshMessageConsumer:
    requires_shutdown_wait = True

    def __init__(
        self,
        broker_client: Any,
        topic: str | None = None,
        logger: Any = None,
        topics: list[str] | tuple[str, ...] | None = None,
        queue_name: str | None = None,
    ) -> None:
        self._broker = broker_client
        self._topics = normalize_topics(topic=topic, topics=topics)
        if not self._topics:
            raise ValueError("EventMeshMessageConsumer requires at least one topic")
        self._topic = self._topics[0]  # backward-compatible attribute
        self._queue_name = queue_name or _resolve_queue_name(broker_client, self._topic)
        self._queue_names_by_topic = {
            topic_name: queue_name or _resolve_queue_name(broker_client, topic_name)
            for topic_name in self._topics
        }
        self._logger = logger
        self._running = False

    async def start(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._running = True

        async def _dispatch(
            raw: Any,
            message_headers: dict[str, str] | None = None,
            queue_name: str | None = None,
        ) -> None:
            resolved_queue_name = queue_name or self._queue_name
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    self._logger.warning(
                        "Received non-JSON message from Event Mesh queue %s — rejecting",
                        resolved_queue_name,
                    )
                    reject = getattr(self._broker, "reject_message", None)
                    ack = getattr(self._broker, "ack_message", None)
                    if reject is not None and message_headers:
                        reject(resolved_queue_name, message_headers, requeue=False)
                    elif ack is not None and message_headers:
                        ack(resolved_queue_name, message_headers)
                    else:
                        self._logger.error(
                            "Broker has no reject or ack method — cannot dispose non-JSON message from Event Mesh queue %s",
                            resolved_queue_name,
                        )
                    return
            correlation_id = (
                raw.get("correlation_id", "") if isinstance(raw, dict) else ""
            )
            self._logger.info(
                "Message received",
                topics=self._topics,
                queue=resolved_queue_name,
                correlation_id=correlation_id,
            )
            try:
                delivery = EventMeshMessageDelivery(
                    broker_client=self._broker,
                    queue_name=resolved_queue_name,
                    payload=raw,
                    message_headers=message_headers or {},
                )
                await handler(delivery)
            except Exception as exc:
                self._logger.error(
                    "Handler error",
                    queue=resolved_queue_name,
                    correlation_id=correlation_id,
                    error=str(exc),
                )

        if has_static_callable_attr(self._broker, "subscribe_to_topics"):
            await asyncio.to_thread(
                self._broker.subscribe_to_topics,
                self._topics,
                _dispatch,
                self._queue_name if self._uses_shared_queue() else None,
            )
        else:
            if has_static_callable_attr(self._broker, "subscribe_to_queue"):
                await asyncio.gather(
                    *(
                        asyncio.to_thread(
                            self._broker.subscribe_to_queue,
                            self._queue_names_by_topic[topic],
                            topic,
                            _dispatch,
                        )
                        for topic in self._topics
                    )
                )
            else:
                await asyncio.gather(
                    *(
                        asyncio.to_thread(
                            self._broker.subscribe_to_topic, topic, _dispatch
                        )
                        for topic in self._topics
                    )
                )
        self._logger.info(
            "EventMeshMessageConsumer started",
            topics=self._topics,
            queue_names=sorted(set(self._queue_names_by_topic.values())),
        )

    def cancel_conversation(self, conv_id: str) -> int:
        """SA-892: delegate a cooperative cancel to the broker's dispatch runtime."""
        cancel = getattr(self._broker, "cancel_conversation", None)
        if callable(cancel):
            return cancel(conv_id)
        return 0

    def _uses_shared_queue(self) -> bool:
        return len(set(self._queue_names_by_topic.values())) == 1

    async def stop(self) -> None:
        self._running = False
        close = getattr(self._broker, "close", None)
        if callable(close):
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
        self._logger.info("EventMeshMessageConsumer stopped", topics=self._topics)
