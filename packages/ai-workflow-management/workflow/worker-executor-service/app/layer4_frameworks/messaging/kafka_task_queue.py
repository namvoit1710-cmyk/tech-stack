"""Kafka implementation of ITaskQueue.

Consumes task messages from a Kafka topic via KafkaBrokerClient using
the subscribe-based callback pattern.
"""

import logging
from typing import Any
from collections.abc import Callable

from app.layer2_application.interfaces.task_queue_interface import ITaskQueue

logger = logging.getLogger(__name__)


class KafkaTaskQueue(ITaskQueue):
    """Consumes tasks from Kafka via subscribe_to_topic."""

    def __init__(self, client: Any, topic: str = "workflow.tasks") -> None:
        self._client = client
        self._topic = topic

    def consume(self, callback: Callable) -> None:
        """Subscribe to the task topic and dispatch messages to *callback*."""
        self._client.subscribe_to_topic(self._topic, callback)
        logger.info("KafkaTaskQueue consuming from topic=%s", self._topic)

    def acknowledge(self, message: Any) -> None:
        """No-op — Kafka auto-commits offsets."""
        pass
