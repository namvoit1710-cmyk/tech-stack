"""SAP Event Mesh implementation of IResultPublisher."""
import dataclasses
from typing import Any

from app.layer2_application.interfaces.message_broker_interface import IMessageBrokerClient
from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher


class EventMeshResultPublisher(IResultPublisher):
    """Publishes task results via a message broker client."""

    TOPIC_SUBJECT = "workflow.results"

    def __init__(self, client: IMessageBrokerClient) -> None:
        self._client = client

    def publish_result(self, result: Any) -> None:
        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            payload = dataclasses.asdict(result)
        elif isinstance(result, dict):
            payload = result
        else:
            payload = {"data": str(result)}

        self._client.publish_to_topic(self.TOPIC_SUBJECT, payload)
