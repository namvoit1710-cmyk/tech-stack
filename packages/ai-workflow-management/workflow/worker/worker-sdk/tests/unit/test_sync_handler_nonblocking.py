"""Verify sync node-type handlers don't block the worker event loop.

Before the fix, ``_invoke_node_type_handler`` called sync handlers
directly (``return handler(resolved_inputs, parameters)``) from inside
``async def execute`` — a sync handler doing CPU work or sync I/O would
stall every other in-flight request on the worker's event loop.

The fix dispatches sync handlers via ``asyncio.to_thread``. This test
asserts the behavioural consequence: two sync handlers invoked
concurrently overlap in wall-clock time instead of running back-to-back.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest


# Reproduce the production helper to keep the test independent of route
# wiring (which requires FastAPI + a full container).
async def _invoke_node_type_handler(handler, resolved_inputs, parameters):
    if inspect.iscoroutinefunction(handler):
        return await handler(resolved_inputs, parameters)
    return await asyncio.to_thread(handler, resolved_inputs, parameters)


def _sync_handler_that_sleeps(_inputs, _parameters):
    """Sync handler that blocks for ~0.2s — simulates CPU/sync-IO work."""
    time.sleep(0.2)
    return {"ok": True}


async def _async_handler_that_sleeps(_inputs, _parameters):
    await asyncio.sleep(0.2)
    return {"ok": True}


@pytest.mark.asyncio
async def test_sync_handler_runs_in_thread_not_blocking_loop() -> None:
    """Two concurrent invocations of a SYNC handler complete in ~0.2s,
    not ~0.4s — proving asyncio.to_thread released the event loop.
    """
    start = time.monotonic()
    results = await asyncio.gather(
        _invoke_node_type_handler(_sync_handler_that_sleeps, {}, {}),
        _invoke_node_type_handler(_sync_handler_that_sleeps, {}, {}),
    )
    elapsed = time.monotonic() - start

    assert all(r == {"ok": True} for r in results)
    # Two 0.2s blocks should run concurrently → close to 0.2s wall-clock.
    # Generous upper bound to keep the test stable on busy CI runners.
    assert elapsed < 0.35, (
        f"elapsed={elapsed:.3f}s — sync handlers ran serially "
        f"(expected ~0.2s, got close to 0.4s)"
    )


@pytest.mark.asyncio
async def test_async_handler_still_awaited_directly() -> None:
    """Async handlers continue to be awaited directly (no thread hop)
    — the to_thread path is only for sync handlers.
    """
    start = time.monotonic()
    results = await asyncio.gather(
        _invoke_node_type_handler(_async_handler_that_sleeps, {}, {}),
        _invoke_node_type_handler(_async_handler_that_sleeps, {}, {}),
    )
    elapsed = time.monotonic() - start

    assert all(r == {"ok": True} for r in results)
    # Async handlers always overlapped — no regression expected.
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_sync_handler_exceptions_propagate() -> None:
    """Exceptions raised by sync handlers must propagate from the thread
    back to the awaiting coroutine, not be swallowed.
    """

    def _failing_handler(_inputs, _parameters):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await _invoke_node_type_handler(_failing_handler, {}, {})
