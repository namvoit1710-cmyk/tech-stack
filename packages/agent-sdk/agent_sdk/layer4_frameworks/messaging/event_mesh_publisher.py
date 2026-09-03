from __future__ import annotations

import asyncio
from typing import Any, Optional


class EventMeshMessagePublisher:
    def __init__(self, broker_client: Any, logger: Any) -> None:
        self._broker = broker_client
        self._logger = logger

    async def publish(
        self, topic: str, message: dict, key: Optional[str] = None
    ) -> None:
        self._logger.info(
            "Publishing message to Event Mesh message = %s",
            message,
            extra={"topic": topic, "key": key},
        )
        await asyncio.to_thread(self._broker.publish_to_topic, topic, message, key=key)

    async def close(self) -> None:
        pass
