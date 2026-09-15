"""In-process response registry for queue-routed HTTP requests.

Phase 3c: PATCH ``/runs/{id}/nodes/{node_id}/data`` is routed through
the orchestration queue so that same-run PATCHes serialise at the
single-consumer worker rather than an in-process ``asyncio.Lock``.
Because PATCH still has HTTP request/response semantics, the
controller needs to await the worker's result.

This registry is a minimal correlation-id → ``asyncio.Future`` map.
The controller allocates a future, enqueues the action carrying the
correlation-id, and awaits. The worker looks up the future by
correlation-id and resolves it with the use-case result.

The futures are PER-PROCESS. Phase M.3.3 added the cross-instance half —
``ResponseTopicListener`` subscribes to this instance's response topic and
resolves the local futures from peer instances' results — so this registry is
no longer "single-instance only" as it was originally written.

What stayed per-process is the *storage*: ``_pending`` is an in-memory dict, so
every pending future dies with the process. That is why SA-2087 (R12) ruled the
instance id must be unique and bounded rather than "stable across restarts":
after a restart there is nothing left to route TO. Surviving a restart would
need durable correlation state, which is deliberately not what this is.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any


class ResponseRegistry:
    """Maps correlation-ids to pending ``asyncio.Future`` objects."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def allocate(self) -> tuple[str, asyncio.Future]:
        """Create a future keyed under a fresh correlation-id. Caller
        ``await``s the future and must ``release(correlation_id)`` in a
        ``finally`` — the worker resolves it via ``resolve`` / ``fail``.
        """
        correlation_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[correlation_id] = future
        return correlation_id, future

    def resolve(self, correlation_id: str, result: Any) -> None:
        """Complete the future for ``correlation_id`` with ``result``.
        No-op if the future is unknown (the controller already gave up
        / timed out) or already done.
        """
        future = self._pending.get(correlation_id)
        if future is not None and not future.done():
            future.set_result(result)

    def fail(self, correlation_id: str, exc: BaseException) -> None:
        """Reject the future for ``correlation_id`` with ``exc``."""
        future = self._pending.get(correlation_id)
        if future is not None and not future.done():
            future.set_exception(exc)

    def release(self, correlation_id: str) -> None:
        """Drop the registration; call from the caller's ``finally``."""
        self._pending.pop(correlation_id, None)
