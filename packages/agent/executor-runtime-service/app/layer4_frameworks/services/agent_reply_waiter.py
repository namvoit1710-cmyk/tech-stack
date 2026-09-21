from __future__ import annotations

import asyncio
import threading
from typing import Any


class AgentReplyWaiter:
    def __init__(self) -> None:
        self._pending: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Future[dict[str, Any]]]
        ] = {}
        self._lock = threading.Lock()

    def register(self, correlation_id: str) -> None:
        if not correlation_id:
            raise ValueError("correlation_id is required")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._lock:
            existing = self._pending.get(correlation_id)
            if existing is not None and not existing[1].done():
                return
            self._pending[correlation_id] = (loop, future)

    async def wait(self, correlation_id: str, timeout_seconds: float) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.get(correlation_id)

        if pending is None:
            raise KeyError(
                f"No pending agent reply for correlation_id='{correlation_id}'"
            )

        _, future = pending
        try:
            return await asyncio.wait_for(future, timeout_seconds)
        finally:
            self.forget(correlation_id)

    def resolve(self, correlation_id: str, message: dict[str, Any]) -> bool:
        with self._lock:
            pending = self._pending.get(correlation_id)

        if pending is None:
            return False

        loop, future = pending
        if future.done():
            return False

        loop.call_soon_threadsafe(future.set_result, message)
        return True

    def reject(self, correlation_id: str, error: Exception | dict[str, Any]) -> bool:
        with self._lock:
            pending = self._pending.get(correlation_id)

        if pending is None:
            return False

        loop, future = pending
        if future.done():
            return False

        if isinstance(error, Exception):
            exc = error
        else:
            exc = RuntimeError(str(error))
        loop.call_soon_threadsafe(future.set_exception, exc)
        return True

    def forget(self, correlation_id: str) -> None:
        with self._lock:
            self._pending.pop(correlation_id, None)
