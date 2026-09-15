"""In-process asyncio.Queue implementation of the orchestration queue.

Used when no external message broker is available (mock / local modes).
"""

from __future__ import annotations

import asyncio
import logging

from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    OrchestrationAction,
)

_logger = logging.getLogger(__name__)


class MemoryOrchestrationQueue(IOrchestrationQueue):
    """Async in-process queue backed by ``asyncio.Queue``."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[OrchestrationAction] = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, action: OrchestrationAction) -> None:
        await self._queue.put(action)
        _logger.debug("Enqueued %s for run %s", action.action_type, action.run_id)

    def enqueue_nowait(self, action: OrchestrationAction) -> None:
        # ``maxsize=0`` (the default) makes the queue unbounded, so
        # ``put_nowait`` never raises ``QueueFull`` — safe to call from a
        # synchronous publisher-tee.
        self._queue.put_nowait(action)
        _logger.debug("Enqueued (nowait) %s for run %s", action.action_type, action.run_id)

    async def dequeue(self) -> OrchestrationAction:
        return await self._queue.get()
