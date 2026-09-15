"""Callback publisher for workflow completion notifications.

Wraps a message broker client to publish WorkflowCompletionMessage payloads
to the agent's callback topic when a workflow run completes or fails.
"""
import logging
from typing import Any

from app.layer2_application.interfaces.callback_publisher_interface import ICallbackPublisher
from app.layer2_application.interfaces.broker_client_interface import IBrokerClient

logger = logging.getLogger(__name__)


class CallbackPublisher(ICallbackPublisher):
    def __init__(self, broker_client: IBrokerClient) -> None:
        self._broker = broker_client

    def publish(self, topic: str, message: dict, key: str = "") -> None:
        try:
            self._broker.publish_to_topic(topic, message, key=key)
            logger.info(
                "CallbackPublisher | published to %s | correlation_id=%s",
                topic, message.get("correlation_id", ""),
            )
        except Exception as exc:
            logger.error(
                "CallbackPublisher | failed to publish to %s: %s", topic, exc,
            )
