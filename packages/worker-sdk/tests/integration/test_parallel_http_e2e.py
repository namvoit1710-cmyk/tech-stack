"""End-to-end proof that N parallel requests overlap in wall-clock.

Spins up the real worker FastAPI app with a registered slow handler,
fires N concurrent ``POST /api/v1/execute`` requests over an in-process
ASGI transport (no network), and asserts wall-clock time is close to
``handler_duration`` — not ``N * handler_duration``.

Three scenarios:
  1. ASYNC handler (awaits ``asyncio.sleep``) — should always parallelise.
  2. SYNC handler (calls ``time.sleep``) — the multi-fix scenario.
     Before the fix this would block the event loop and run serially;
     after the fix ``asyncio.to_thread`` releases the loop so requests
     overlap.
  3. MIXED traffic — async and sync handlers in the same burst.

This is the empirical companion to ``test_sync_handler_nonblocking.py``
(unit-level proof of the wrap) and demonstrates the property at the
full HTTP boundary.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer1_domain.entities.node_type_definition import (
    NodeTypeDefinition,
)
from worker_sdk.layer3_adapters.controllers.worker_server import (
    create_worker_app,
)


_HANDLER_DELAY_S = 0.3
_CONCURRENT = 5
# Wall-clock budget: serial would be N * delay; parallel should be close
# to one delay. Use a generous margin so the test is stable on slow CI.
_PARALLEL_MAX_S = _HANDLER_DELAY_S * 2.0
_SERIAL_MIN_S = _HANDLER_DELAY_S * _CONCURRENT * 0.5


def _make_sync_handler():
    """Sync handler that sleeps — used to be the loop-blocker."""

    def handler(inputs, parameters):
        time.sleep(_HANDLER_DELAY_S)
        return {"echo": inputs.get("key", "")}

    return handler


def _make_async_handler():
    """Async handler with proper await — has always parallelised."""

    async def handler(inputs, parameters):
        await asyncio.sleep(_HANDLER_DELAY_S)
        return {"echo": inputs.get("key", "")}

    return handler


def _build_app_with_handler(*, worker_type: str, handler):
    """Build the worker app with a single NodeTypeDefinition registered."""
    nt = NodeTypeDefinition(
        worker_type=worker_type,
        name=f"slow-{worker_type}",
        handler=handler,
    )
    container = build_app_container(node_types=[nt])
    return create_worker_app(container)


async def _fire_n(app, *, worker_type: str, n: int) -> list[dict]:
    """Send ``n`` concurrent ``POST /api/v1/execute`` requests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        timeout=10.0,
    ) as client:
        async def _one(i: int) -> dict:
            response = await client.post(
                "/api/v1/execute",
                json={
                    "task_id": f"t-{i}",
                    "action": "test",
                    "inputs": {"key": f"value-{i}"},
                    "parameters": {},
                    "worker_type": worker_type,
                },
            )
            assert response.status_code == 200
            return response.json()

        return await asyncio.gather(*(_one(i) for i in range(n)))


@pytest.mark.asyncio
async def test_n_concurrent_async_handlers_overlap_in_wallclock() -> None:
    """N concurrent requests to an async handler complete in ~handler_delay,
    not N × handler_delay.
    """
    app = _build_app_with_handler(
        worker_type="slow-async",
        handler=_make_async_handler(),
    )

    start = time.monotonic()
    results = await _fire_n(app, worker_type="slow-async", n=_CONCURRENT)
    elapsed = time.monotonic() - start

    assert len(results) == _CONCURRENT
    assert all(r.get("status") == "success" for r in results)
    assert all(
        r["outputs"].get("echo") == f"value-{i}"
        for i, r in enumerate(results)
    )

    assert elapsed < _PARALLEL_MAX_S, (
        f"elapsed={elapsed:.3f}s — async handlers should run concurrently "
        f"in ~{_HANDLER_DELAY_S}s, expected < {_PARALLEL_MAX_S}s"
    )


@pytest.mark.asyncio
async def test_n_concurrent_sync_handlers_overlap_in_wallclock() -> None:
    """N concurrent requests to a SYNC handler complete in ~handler_delay,
    not N × handler_delay — proving ``asyncio.to_thread`` released the loop.

    Pre-fix this would have run serially because the sync handler blocked
    the event loop. Post-fix the SDK wraps sync handlers in to_thread, so
    multiple sync handlers run on threads concurrently.
    """
    app = _build_app_with_handler(
        worker_type="slow-sync",
        handler=_make_sync_handler(),
    )

    start = time.monotonic()
    results = await _fire_n(app, worker_type="slow-sync", n=_CONCURRENT)
    elapsed = time.monotonic() - start

    assert len(results) == _CONCURRENT
    assert all(r.get("status") == "success" for r in results)

    assert elapsed < _PARALLEL_MAX_S, (
        f"elapsed={elapsed:.3f}s — sync handlers blocked the event loop "
        f"(would expect ~{_HANDLER_DELAY_S}s parallel, got close to "
        f"{_CONCURRENT * _HANDLER_DELAY_S:.1f}s which means SERIAL)"
    )


@pytest.mark.asyncio
async def test_mixed_async_and_sync_handlers_all_overlap() -> None:
    """Sync and async handlers on the same app — all N requests overlap.

    Builds an app with two registered node types: one async, one sync.
    Fires N/2 of each in a single concurrent burst. Total wall-clock
    should still be ~handler_delay because async ones overlap with each
    other AND with the sync ones (sync goes to thread pool, async stays
    on the loop, both make progress concurrently).
    """
    nt_async = NodeTypeDefinition(
        worker_type="mixed-async",
        name="mixed-async",
        handler=_make_async_handler(),
    )
    nt_sync = NodeTypeDefinition(
        worker_type="mixed-sync",
        name="mixed-sync",
        handler=_make_sync_handler(),
    )
    container = build_app_container(node_types=[nt_async, nt_sync])
    app = create_worker_app(container)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        timeout=10.0,
    ) as client:
        async def _one(worker_type: str, i: int) -> dict:
            response = await client.post(
                "/api/v1/execute",
                json={
                    "task_id": f"t-{worker_type}-{i}",
                    "action": "test",
                    "inputs": {"key": f"{worker_type}-{i}"},
                    "parameters": {},
                    "worker_type": worker_type,
                },
            )
            assert response.status_code == 200
            return response.json()

        half = _CONCURRENT // 2
        start = time.monotonic()
        results = await asyncio.gather(
            *(_one("mixed-async", i) for i in range(half)),
            *(_one("mixed-sync", i) for i in range(half)),
        )
        elapsed = time.monotonic() - start

    assert len(results) == 2 * half
    assert all(r.get("status") == "success" for r in results)

    assert elapsed < _PARALLEL_MAX_S, (
        f"elapsed={elapsed:.3f}s — mixed async+sync handlers did not "
        f"overlap (expected ~{_HANDLER_DELAY_S}s, got close to serial)"
    )


@pytest.mark.asyncio
async def test_sync_handler_results_are_correct_under_concurrency() -> None:
    """Concurrent invocations of a sync handler each return their own
    inputs (no cross-talk between threads).
    """
    app = _build_app_with_handler(
        worker_type="echo-sync",
        handler=_make_sync_handler(),
    )

    results = await _fire_n(app, worker_type="echo-sync", n=_CONCURRENT)

    echoed = [r["outputs"].get("echo") for r in results]
    assert sorted(echoed) == sorted(f"value-{i}" for i in range(_CONCURRENT)), (
        f"Results cross-talked between concurrent invocations: {echoed}"
    )
