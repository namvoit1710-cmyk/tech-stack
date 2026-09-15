import logging
from typing import Any

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher

logger = logging.getLogger(__name__)


class ConsoleEventPublisher(IEventPublisher):
    def publish(self, topic: str, event: Any) -> None:
        logger.debug("[EVENT] topic=%s event=%s", topic, event)
