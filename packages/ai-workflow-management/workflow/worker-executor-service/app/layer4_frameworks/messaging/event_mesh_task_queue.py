"""SAP Event Mesh implementation of ITaskQueue."""
from typing import Any
from collections.abc import Callable

from app.layer2_application.interfaces.message_broker_interface import IMessageBrokerClient
from app.layer2_application.interfaces.task_queue_interface import ITaskQueue


class EventMeshTaskQueue(ITaskQueue):
    """Consumes task messages dispatched to the ``workflow.tasks`` topic.

    Uses ``subscribe_to_topic`` (continuous, auto-creating queue/subscription
    and a background poll loop with auto-ack) to match ``KafkaTaskQueue``.
    """

    TOPIC_SUBJECT = "workflow.tasks"

    def __init__(self, client: IMessageBrokerClient) -> None:
        self._client = client

    def consume(self, callback: Callable) -> None:
        self._client.subscribe_to_topic(self.TOPIC_SUBJECT, callback)

    def acknowledge(self, message: Any) -> None:
        # subscribe_to_topic auto-acknowledges; nothing to do here.
        pass
