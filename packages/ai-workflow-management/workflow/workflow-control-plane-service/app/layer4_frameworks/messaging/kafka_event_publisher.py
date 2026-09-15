"""Kafka implementation of IEventPublisher.

Publishes DomainEvent instances to Kafka topics via KafkaBrokerClient.
Centralized topic publishing is handled by EnrichingEventPublisher decorator.
"""

import dataclasses
import logging
from typing import Any

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient

logger = logging.getLogger(__name__)


def extract_partition_key(payload: dict) -> str | None:
    """Phase M.3.4: pick the partition key that pins every event for one
    run to a single Kafka partition. Child runs stick with their parent
    via ``root_run_id`` first. Falls back to run_id, then workflow_id.
    Returns ``None`` when none are present — Kafka will round-robin.

    Mirrors the precedent set by ``RealtimeBroadcastingPublisher``; keep
    the rules aligned so per-run ordering composes across both chains.
    """
    for field in ("root_run_id", "run_id", "workflow_id"):
        value = payload.get(field)
        if value:
            return str(value)
    return None


class KafkaEventPublisher(IEventPublisher):
    """Publishes domain events to Kafka topics."""

    def __init__(self, client: IBrokerClient) -> None:
        self._client = client

    def publish(self, topic: str, event: Any) -> None:
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            payload = dataclasses.asdict(event)
        elif isinstance(event, dict):
            payload = event
        else:
            payload = {"data": str(event)}

        key = extract_partition_key(payload) if isinstance(payload, dict) else None
        logger.debug("Publishing event to Kafka: topic=%s event=%s key=%s", topic, event, key)
        self._client.publish_to_topic(topic, payload, key=key)
