"""SAP Event Mesh implementation of IEventPublisher.

Publishes DomainEvent instances to Event Mesh topics.
Centralized topic publishing is handled by EnrichingEventPublisher decorator.
"""

import dataclasses
import logging
from typing import Any

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient

logger = logging.getLogger(__name__)


class EventMeshEventPublisher(IEventPublisher):
    """Publishes domain events to SAP Event Mesh topics."""

    def __init__(self, client: IBrokerClient) -> None:
        self._client = client

    def publish(self, topic: str, event: Any) -> None:
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            payload = dataclasses.asdict(event)
        elif isinstance(event, dict):
            payload = event
        else:
            payload = {"data": str(event)}

        self._client.publish_to_topic(topic, payload)
