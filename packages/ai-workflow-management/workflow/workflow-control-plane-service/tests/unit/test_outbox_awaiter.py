"""Phase 3 Sub 3.3 — OutboxAwaiter."""

from __future__ import annotations

import asyncio

import pytest

from app.layer2_application.events.outbox_awaiter import (
    OutboxAwaiter,
    OutboxResolution,
    OutboxTimeoutError,
)


@pytest.mark.asyncio
class TestAwaiter:
    async def test_register_then_deliver_resolves(self):
        a = OutboxAwaiter()
        a.register("ob_1")
        a.deliver("ob_1", outcome="ok", result={"file_id": "f"})
        res = await asyncio.wait_for(a.wait("ob_1", timeout=1), timeout=2)
        assert res.outcome == "ok"
        assert res.result == {"file_id": "f"}

    async def test_wait_blocks_until_deliver(self):
        a = OutboxAwaiter()
        a.register("ob_1")

        async def deliver_after_delay():
            await asyncio.sleep(0.05)
            a.deliver("ob_1", outcome="ok", result={"x": 1})

        asyncio.create_task(deliver_after_delay())
        res = await a.wait("ob_1", timeout=1.0)
        assert res.result == {"x": 1}

    async def test_timeout_raises(self):
        a = OutboxAwaiter()
        a.register("ob_1")
        with pytest.raises(OutboxTimeoutError):
            await a.wait("ob_1", timeout=0.05)

    async def test_failed_outcome_propagates(self):
        a = OutboxAwaiter()
        a.register("ob_1")
        a.deliver("ob_1", outcome="failed", result={}, error="boom")
        res = await a.wait("ob_1", timeout=1.0)
        assert res.outcome == "failed"
        assert res.error == "boom"

    async def test_deliver_unknown_id_is_noop(self):
        a = OutboxAwaiter()
        a.deliver("ob_unknown", outcome="ok", result={})
        # No exception, no waiter — pass.

    async def test_wait_without_register_creates_future(self):
        a = OutboxAwaiter()
        # If a deliver lands before any register, the wait still
        # registers and times out — replay finds the resolved event in
        # the log.
        with pytest.raises(OutboxTimeoutError):
            await a.wait("ob_1", timeout=0.05)

    async def test_pending_count_drops_after_wait(self):
        a = OutboxAwaiter()
        a.register("ob_1")
        a.deliver("ob_1", outcome="ok", result={})
        await a.wait("ob_1", timeout=1.0)
        assert a.pending_count() == 0

    async def test_double_deliver_is_safe(self):
        a = OutboxAwaiter()
        a.register("ob_1")
        a.deliver("ob_1", outcome="ok", result={"a": 1})
        # Future already resolved — second deliver is no-op.
        a.deliver("ob_1", outcome="ok", result={"a": 2})
        res = await a.wait("ob_1", timeout=1.0)
        assert res.result == {"a": 1}
