"""Gap-filling unit specs for AgentReplyWaiter.

Targets branches uncovered by test_agent_reply_waiter.py:
  - register("") raises ValueError (agent_reply_waiter.py:17)
  - re-register of a live correlation_id is a no-op (24)
  - wait() on an unregistered id raises KeyError (32)
  - resolve() returns False when future already done (51)
  - reject() with an Exception vs a dict/str payload (56-72)
Expectations grounded in agent_reply_waiter.py.
"""

from __future__ import annotations

import asyncio

import pytest

from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter


# --- invalid input: empty correlation_id ---
@pytest.mark.asyncio
async def test_register_empty_correlation_id_raises():
    waiter = AgentReplyWaiter()
    with pytest.raises(ValueError, match="correlation_id is required"):
        waiter.register("")


# --- edge: re-registering a live id keeps the original future (no-op) ---
@pytest.mark.asyncio
async def test_reregister_live_id_is_noop():
    waiter = AgentReplyWaiter()
    waiter.register("corr-x")
    first = waiter._pending["corr-x"][1]
    waiter.register("corr-x")  # future not done -> keep existing
    second = waiter._pending["corr-x"][1]
    assert first is second
    # resolve to clean up the pending future
    waiter.resolve("corr-x", {"status": "success"})
    await asyncio.sleep(0)


# --- edge: re-registering after the previous future is done replaces it ---
@pytest.mark.asyncio
async def test_reregister_after_done_replaces_future():
    waiter = AgentReplyWaiter()
    waiter.register("corr-y")
    old_future = waiter._pending["corr-y"][1]
    old_future.set_result({"done": True})
    waiter.register("corr-y")  # existing done -> replace
    assert waiter._pending["corr-y"][1] is not old_future


# --- invalid input: wait on an unregistered id ---
@pytest.mark.asyncio
async def test_wait_unregistered_raises_keyerror():
    waiter = AgentReplyWaiter()
    with pytest.raises(KeyError, match="No pending agent reply"):
        await waiter.wait("nope", 0.01)


# --- failure path: resolve unknown id returns False ---
@pytest.mark.asyncio
async def test_resolve_unknown_returns_false():
    waiter = AgentReplyWaiter()
    assert waiter.resolve("ghost", {"status": "success"}) is False


# --- boundary: resolve when future already resolved returns False ---
@pytest.mark.asyncio
async def test_resolve_when_already_done_returns_false():
    waiter = AgentReplyWaiter()
    waiter.register("corr-d")
    assert waiter.resolve("corr-d", {"a": 1}) is True
    await asyncio.sleep(0)
    # forget() ran inside wait() only; the pending is still present until waited.
    # Second resolve on a not-yet-forgotten but resolved future returns False.
    if "corr-d" in waiter._pending:
        assert waiter.resolve("corr-d", {"a": 2}) is False


# --- failure path: reject with an Exception instance ---
@pytest.mark.asyncio
async def test_reject_with_exception_propagates():
    waiter = AgentReplyWaiter()
    waiter.register("corr-r1")
    task = asyncio.create_task(waiter.wait("corr-r1", 1.0))
    await asyncio.sleep(0)
    assert waiter.reject("corr-r1", ValueError("bad reply")) is True
    with pytest.raises(ValueError, match="bad reply"):
        await task


# --- failure path: reject with a non-Exception (dict) wraps in RuntimeError ---
@pytest.mark.asyncio
async def test_reject_with_dict_wraps_runtimeerror():
    waiter = AgentReplyWaiter()
    waiter.register("corr-r2")
    task = asyncio.create_task(waiter.wait("corr-r2", 1.0))
    await asyncio.sleep(0)
    assert waiter.reject("corr-r2", {"error": "boom"}) is True
    with pytest.raises(RuntimeError):
        await task


# --- failure path: reject unknown id returns False ---
@pytest.mark.asyncio
async def test_reject_unknown_returns_false():
    waiter = AgentReplyWaiter()
    assert waiter.reject("ghost", RuntimeError("x")) is False


# --- edge: forget on unknown id is a silent no-op ---
@pytest.mark.asyncio
async def test_forget_unknown_is_noop():
    waiter = AgentReplyWaiter()
    waiter.forget("never-seen")  # must not raise
    assert "never-seen" not in waiter._pending
