"""Gap-filling unit specs for TechnicalAgentStepExecutor.

Targets branches uncovered by test_technical_agent_step_executor.py:
  - execute_by not agent-prefixed -> invalid execute_by error (executor.py:107-112 / _parse_agent_id 378)
  - delegator.delegate raises before wait -> forget + error (164-172)
  - reply_waiter.wait raises a non-Timeout exception -> error (205-214)
  - _normalize_reply: unsupported status (348), error-dict stringify (388/393-394),
    non-mapping output coerced (341)
Collaborators: delegator (async .delegate) -> AsyncMock; reply_waiter -> real
AgentReplyWaiter (register/resolve/forget are sync, wait is async); logger -> MagicMock.
Expectations grounded in technical_agent_step_executor.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.step import (
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepStatus,
    DispatchStepRequest,
)
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter
from app.layer4_frameworks.services.technical_agent_step_executor import (
    TechnicalAgentStepExecutor,
)

AGENT_EXECUTE_BY = "agent.11111111-1111-4111-8111-111111111111"


def _request(execute_by=AGENT_EXECUTE_BY):
    step = Step(id="step_1", execute_by=execute_by, input_data={}, goal="g", message="m")
    return DispatchStepRequest(
        step=step,
        message_id="msg-1",
        correlation_id="corr-1",
        conv_id="conv-1",
        batch_id="batch-1",
        from_agent="agent.business",
    )


def _capability():
    return ResolvedCapability(
        capability_id="cap-1",
        execution_target=ExecutionTarget.TECHNICAL_AGENT,
        agent_type="file-agent",
        enabled=True,
    )


def _executor(delegator, waiter, timeout=1.0):
    return TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=timeout,
        logger=MagicMock(),
    )


# --- invalid input: execute_by not agent-prefixed (lines 107-112) ---
@pytest.mark.asyncio
async def test_execute_by_not_agent_prefixed_errors():
    delegator = AsyncMock()
    executor = _executor(delegator, AgentReplyWaiter())
    result = await executor.execute(
        _request(execute_by="tool.11111111-1111-4111-8111-111111111111"),
        _capability(),
    )
    assert result.status == StepStatus.ERROR
    assert "Invalid technical agent execute_by" in result.error
    delegator.delegate.assert_not_awaited()


# --- invalid input: execute_by 'agent.' with empty id ---
@pytest.mark.asyncio
async def test_execute_by_agent_empty_id_errors():
    delegator = AsyncMock()
    executor = _executor(delegator, AgentReplyWaiter())
    result = await executor.execute(_request(execute_by="agent."), _capability())
    assert result.status == StepStatus.ERROR
    assert "Invalid technical agent execute_by" in result.error


# --- failure path: delegator.delegate raises before wait (lines 164-176) ---
@pytest.mark.asyncio
async def test_delegate_raises_before_wait_forgets_and_errors():
    waiter = AgentReplyWaiter()
    delegator = AsyncMock()
    delegator.delegate.side_effect = RuntimeError("broker unavailable")
    executor = _executor(delegator, waiter)

    result = await executor.execute(_request(), _capability())

    assert result.status == StepStatus.ERROR
    assert "Technical agent delegation failed" in result.error
    assert "broker unavailable" in result.error
    # correlation was forgotten -> nothing pending
    assert waiter._pending == {}


# --- failure path: waiter.wait raises a non-timeout exception (lines 205-218) ---
@pytest.mark.asyncio
async def test_wait_raises_non_timeout_errors():
    waiter = AgentReplyWaiter()
    delegator = AsyncMock()
    executor = _executor(delegator, waiter)

    task = asyncio.create_task(executor.execute(_request(), _capability()))
    await asyncio.sleep(0)
    # find the registered correlation id and reject with a non-timeout error
    corr = next(iter(waiter._pending))
    waiter.reject(corr, RuntimeError("reply channel exploded"))

    result = await task
    assert result.status == StepStatus.ERROR
    assert "Technical agent reply failed" in result.error
    assert "reply channel exploded" in result.error


# --- edge: unsupported terminal status maps to error (line 347-348) ---
@pytest.mark.asyncio
async def test_unsupported_status_maps_to_error():
    waiter = AgentReplyWaiter()
    delegator = AsyncMock()
    executor = _executor(delegator, waiter)

    task = asyncio.create_task(executor.execute(_request(), _capability()))
    await asyncio.sleep(0)
    corr = next(iter(waiter._pending))
    waiter.resolve(
        corr,
        {"payload": {"step": {"id": "step_1", "status": "cancelled"}}},
    )
    result = await task
    assert result.status == StepStatus.ERROR
    assert "unsupported status 'cancelled'" in result.error


# --- edge: error reported as a bare string (stringify path, lines 386-387) ---
@pytest.mark.asyncio
async def test_error_string_stringified():
    waiter = AgentReplyWaiter()
    delegator = AsyncMock()
    executor = _executor(delegator, waiter)

    task = asyncio.create_task(executor.execute(_request(), _capability()))
    await asyncio.sleep(0)
    corr = next(iter(waiter._pending))
    waiter.resolve(
        corr,
        {"payload": {"step": {"id": "step_1", "status": "success", "error": "plain error"}}},
    )
    result = await task
    assert result.status == StepStatus.ERROR
    assert result.error == "plain error"


# --- boundary: non-mapping output_data coerced to {"value": ...} (line 341/384) ---
@pytest.mark.asyncio
async def test_non_mapping_output_coerced():
    waiter = AgentReplyWaiter()
    delegator = AsyncMock()
    executor = _executor(delegator, waiter)

    task = asyncio.create_task(executor.execute(_request(), _capability()))
    await asyncio.sleep(0)
    corr = next(iter(waiter._pending))
    waiter.resolve(
        corr,
        {"payload": {"step": {"id": "step_1", "status": "success"}}, "output": "scalar-out"},
    )
    result = await task
    assert result.status == StepStatus.SUCCESS
    assert result.output == {"value": "scalar-out"}
