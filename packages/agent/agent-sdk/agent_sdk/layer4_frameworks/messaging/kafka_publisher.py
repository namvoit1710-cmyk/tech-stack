from __future__ import annotations

import asyncio
from typing import Any, Optional


class KafkaMessagePublisher:
    def __init__(self, broker_client: Any, logger: Any = None) -> None:
        self._broker = broker_client
        self._logger = logger
        self._known_topics: set[str] = set()
        self._topic_locks: dict[str, asyncio.Lock] = {}

    async def publish(
        self,
        topic: str,
        message: dict,
        key: Optional[str] = None,
    ) -> None:
        ensure_topic_exists = getattr(self._broker, "ensure_topic_exists", None)
        if callable(ensure_topic_exists):
            lock = self._topic_locks.get(topic)
            if lock is None:
                lock = asyncio.Lock()
                self._topic_locks[topic] = lock

            async with lock:
                if topic not in self._known_topics:
                    try:
                        await asyncio.to_thread(ensure_topic_exists, topic)
                    except Exception:
                        self._known_topics.discard(topic)
                        raise
                    self._known_topics.add(topic)

        if self._logger:
            self._logger.info("Publishing message to Kafka", topic=topic, key=key)

        await asyncio.to_thread(
            self._broker.publish_to_topic,
            topic,
            message,
            key=key,
        )

    async def close(self) -> None:
        flush_producer = getattr(self._broker, "flush_producer", None)
        if callable(flush_producer):
            await asyncio.to_thread(flush_producer, 10.0)
