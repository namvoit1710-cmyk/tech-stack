from __future__ import annotations

from typing import Any, Awaitable, Callable


class _MockDelivery:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def ack(self) -> None:
        return None

    async def nack(self, requeue: bool = True) -> None:
        return None

    async def reject(self) -> None:
        return None


class MockMessageConsumer:
    def __init__(self, logger: Any) -> None:
        self._logger = logger

    async def start(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._logger.info("MockMessageConsumer started (no-op)")

    async def stop(self) -> None:
        self._logger.info("MockMessageConsumer stopped (no-op)")
