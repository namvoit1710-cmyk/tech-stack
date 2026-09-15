"""Kafka implementation of IResultPublisher.

Publishes task results to the Kafka results topic via KafkaBrokerClient.
"""

import dataclasses
import logging
from typing import Any

from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher

logger = logging.getLogger(__name__)


class KafkaResultPublisher(IResultPublisher):
    """Publishes task results to Kafka."""

    def __init__(self, client: Any, topic: str = "workflow.results") -> None:
        self._client = client
        self._topic = topic

    def publish_result(self, result: Any) -> None:
        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            payload = dataclasses.asdict(result)
        elif isinstance(result, dict):
            payload = result
        else:
            payload = {"data": str(result)}

        # SA-1941 — log a summary, not the full result (large inline output).
        # Real callers publish a nested envelope
        # ``{message_type, correlation_id, payload: {task_id, ...}}``, so the
        # task_id lives one level down; fall back to the top level / correlation.
        tid = "?"
        if isinstance(payload, dict):
            tid = (
                (payload.get("payload") or {}).get("task_id")
                or payload.get("task_id")
                or payload.get("correlation_id")
                or "?"
            )
        logger.info("Publishing result to Kafka: topic=%s task_id=%s", self._topic, tid)
        self._client.publish_to_topic(self._topic, payload)
