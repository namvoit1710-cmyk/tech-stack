from __future__ import annotations

import asyncio

import pytest

from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter


@pytest.mark.asyncio
async def test_agent_reply_waiter_resolves_reply():
    waiter = AgentReplyWaiter()
    correlation_id = "corr-1"

    waiter.register(correlation_id)
    task = asyncio.create_task(waiter.wait(correlation_id, 1.0))

    await asyncio.sleep(0)
    waiter.resolve(correlation_id, {"status": "success", "output_data": {"ok": True}})

    reply = await task

    assert reply["output_data"]["ok"] is True


@pytest.mark.asyncio
async def test_agent_reply_waiter_times_out_and_cleans_up():
    waiter = AgentReplyWaiter()
    correlation_id = "corr-2"

    waiter.register(correlation_id)

    with pytest.raises(TimeoutError):
        await waiter.wait(correlation_id, 0.01)

    assert waiter.resolve(correlation_id, {"status": "success"}) is False
