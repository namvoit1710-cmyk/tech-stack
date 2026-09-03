from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)

logger = logging.getLogger(__name__)
_STOP = object()


class BufferedPushGatewayNotifier(PushGatewayNotifierProtocol):
    def __init__(
        self,
        *,
        inner: PushGatewayNotifierProtocol,
        max_size: int = 256,
        enqueue_timeout_ms: int = 50,
        drain_timeout_seconds: int = 5,
        logger: Any | None = None,
    ) -> None:
        self._inner = inner
        self._max_size = max(1, max_size)
        self._enqueue_timeout_seconds = max(enqueue_timeout_ms, 0) / 1000.0
        self._drain_timeout_seconds = max(drain_timeout_seconds, 0)
        self._logger = logger or globals()["logger"]
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._max_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._closing = False

    async def send_notification(
        self,
        key: str,
        data: Any,
        *,
        is_final: bool = False,
    ) -> None:
        if self._closing:
            await self._inner.send_notification(key, data, is_final=is_final)
            return

        self._ensure_worker()
        item = (key, data, is_final)
        try:
            await asyncio.wait_for(
                self._queue.put(item),
                timeout=self._enqueue_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._logger.warning(
                "Buffered push gateway queue full; falling back to inline delivery for key=%s",
                key,
            )
            await self._inner.send_notification(key, data, is_final=is_final)

    async def close(self) -> None:
        self._closing = True
        if self._worker_task is not None:
            try:
                self._ensure_worker()
                await asyncio.wait_for(
                    self._queue.join(),
                    timeout=self._drain_timeout_seconds,
                )
                await self._queue.put(_STOP)
                await self._worker_task
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Timed out draining buffered push gateway notifications during close"
                )
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            finally:
                self._worker_task = None
        await self._inner.close()

    def _ensure_worker(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        if self._worker_task is not None and self._worker_task.done():
            self._logger.warning("Buffered push gateway worker exited; recreating it")
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                key, data, is_final = cast(tuple[str, Any, bool], item)
                await self._inner.send_notification(key, data, is_final=is_final)
            finally:
                self._queue.task_done()
