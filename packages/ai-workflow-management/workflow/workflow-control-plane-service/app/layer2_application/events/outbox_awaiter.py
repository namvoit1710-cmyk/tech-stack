"""Outbox awaiter — Phase 3 Sub 3.3.

Producer-side helper. After a handler appends ``OutboxRequested`` to
the event log, it ``await``s ``wait(outbox_id, timeout)`` to block
until the dispatcher writes the matching ``OutboxResolved``.

Mirrors the ``ResponseRegistry`` shape from Phase 3c: an in-process
``outbox_id → asyncio.Future`` map. The projector (or whoever folds
``OutboxResolved`` events) calls ``deliver`` with the resolved
payload to release the future.

Single-process semantics. Cross-instance resolution requires a
broker response topic, same constraint as ``ResponseRegistry``;
acceptable until Kyma migration per the M.1 sticky-routing memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutboxResolution:
    """The payload delivered to a waiter on ``OutboxResolved`` fold."""

    outbox_id: str
    outcome: str            # "ok" | "failed"
    result: dict[str, Any]
    error: str | None = None


class OutboxTimeoutError(Exception):
    """Raised by ``wait`` when ``timeout`` elapses with no resolution."""


class OutboxAwaiter:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[OutboxResolution]] = {}

    def register(self, outbox_id: str) -> asyncio.Future[OutboxResolution]:
        """Allocate a future for ``outbox_id`` BEFORE appending the
        ``OutboxRequested`` event.

        Calling order matters: register first so a same-tick
        ``deliver`` (e.g. the dispatcher loop ran between append and
        wait) lands on a known future rather than being dropped.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[outbox_id] = future
        return future

    async def wait(
        self, outbox_id: str, *, timeout: float,
    ) -> OutboxResolution:
        """Block until the dispatcher resolves ``outbox_id``.

        Raises ``OutboxTimeoutError`` if no resolution arrives within
        ``timeout`` seconds. Caller should treat the timeout as
        "fall back to async response" (HTTP 202 path); the resolution
        will eventually fold into the projector's view-store.
        """
        future = self._pending.get(outbox_id)
        if future is None:
            future = self.register(outbox_id)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise OutboxTimeoutError(
                f"outbox_id={outbox_id} not resolved within {timeout}s",
            ) from exc
        finally:
            self._pending.pop(outbox_id, None)

    def deliver(
        self,
        outbox_id: str,
        *,
        outcome: str,
        result: dict[str, Any],
        error: str | None = None,
    ) -> None:
        """Resolve any pending future for ``outbox_id``.

        No-op when no waiter is registered — a resolution arriving
        before the producer set up its waiter (rare, only on replay
        of a fully-resolved entry) just falls through; the producer
        then re-runs and finds the resolved ``OutboxResolved`` in the
        event log directly.
        """
        future = self._pending.get(outbox_id)
        if future is None or future.done():
            return
        future.set_result(
            OutboxResolution(
                outbox_id=outbox_id,
                outcome=outcome,
                result=result,
                error=error,
            ),
        )

    def cancel(self, outbox_id: str, exc: BaseException) -> None:
        """Reject the pending future — used when the dispatcher logs a
        permanent error before the waiter shows up."""
        future = self._pending.get(outbox_id)
        if future is not None and not future.done():
            future.set_exception(exc)

    def pending_count(self) -> int:
        return len(self._pending)
