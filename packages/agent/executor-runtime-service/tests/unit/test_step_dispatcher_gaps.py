"""Gap-filling unit specs for DispatchStepUseCase.

Targets branches uncovered by test_step_dispatcher.py:
  - unknown execution_target (dispatch_step_use_case.py:83-86)
  - local tool executor raising an exception (dispatch_step_use_case.py:110-114)
  - technical-agent delegation with no injected executor + None input_data (boundary)

Five case types are exercised across this module together with the sibling
test_step_dispatcher.py: happy (sibling), edge, invalid input, boundary, failure path.
Expectations grounded in the source (dispatch_step_use_case.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.step import (
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
    DispatchStepRequest,
)
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)

TOOL_ID = "tool.11111111-1111-4111-8111-111111111111"
AGENT_ID = "agent.22222222-2222-4222-8222-222222222222"


def _build_request(step: Step) -> DispatchStepRequest:
    return DispatchStepRequest(
        step=step,
        message_id="msg_g",
        correlation_id="corr_g",
        conv_id="conv_g",
        batch_id="batch_g",
        from_agent="agent.business",
    )


def _uc(resolver, tool_executor, technical=None):
    # capability_resolver.resolve is async -> AsyncMock; tool_executor.execute is
    # async -> AsyncMock; logger is sync -> MagicMock.
    return DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=MagicMock(),
        technical_agent_step_executor=technical,
    )


# --- invalid input / edge: unknown execution_target enum value (lines 83-86) ---
@pytest.mark.asyncio
async def test_dispatch_unknown_execution_target_returns_error():
    # Force a capability whose execution_target is neither LOCAL_TOOL nor
    # TECHNICAL_AGENT so the final fall-through branch runs.
    bogus_target = MagicMock()
    bogus_target.value = "MYSTERY"
    capability = ResolvedCapability(
        capability_id=TOOL_ID,
        execution_target=bogus_target,
        tool_name="echo",
        enabled=True,
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = capability
    tool_executor = AsyncMock()

    uc = _uc(resolver, tool_executor)
    step = Step(id="step_unknown", execute_by=TOOL_ID)
    result = await uc.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert "Unknown execution_target" in result.error
    tool_executor.execute.assert_not_awaited()


# --- failure path: local tool executor raises (lines 110-114) ---
@pytest.mark.asyncio
async def test_dispatch_local_tool_executor_raises_is_wrapped():
    capability = ResolvedCapability(
        capability_id=TOOL_ID,
        execution_target=ExecutionTarget.LOCAL_TOOL,
        tool_name="echo",
        enabled=True,
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = capability
    tool_executor = AsyncMock()
    tool_executor.execute.side_effect = RuntimeError("tool blew up")

    uc = _uc(resolver, tool_executor)
    step = Step(id="step_boom", execute_by=TOOL_ID)
    result = await uc.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert result.step_id == "step_boom"
    assert "Local tool execution failed" in result.error
    assert "tool blew up" in result.error


# --- failure path: capability resolver raises propagates (not swallowed by uc) ---
@pytest.mark.asyncio
async def test_dispatch_resolver_error_propagates():
    resolver = AsyncMock()
    resolver.resolve.side_effect = ConnectionError("registry down")
    tool_executor = AsyncMock()

    uc = _uc(resolver, tool_executor)
    step = Step(id="step_reg", execute_by=TOOL_ID)
    with pytest.raises(ConnectionError):
        await uc.dispatch(_build_request(step))


# --- happy path: technical agent delegated via injected executor with None input_data ---
@pytest.mark.asyncio
async def test_dispatch_technical_agent_none_input_data_boundary():
    capability = ResolvedCapability(
        capability_id=AGENT_ID,
        execution_target=ExecutionTarget.TECHNICAL_AGENT,
        agent_type="file-agent",
        enabled=True,
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = capability
    technical = AsyncMock()
    technical.execute.return_value = StepResult(
        step_id="step_agent", status=StepStatus.SUCCESS, output={"ok": True}
    )

    uc = _uc(resolver, AsyncMock(), technical=technical)
    # input_data explicitly None to exercise `dict(step.input_data or {})` guard (line 135).
    step = Step(id="step_agent", execute_by=AGENT_ID)
    step.input_data = None  # type: ignore[assignment]
    result = await uc.dispatch(_build_request(step))

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"ok": True}
    technical.execute.assert_awaited_once()


# --- boundary: local tool executor returns an ERROR StepResult unchanged (pass-through) ---
@pytest.mark.asyncio
async def test_dispatch_local_tool_error_result_passed_through():
    capability = ResolvedCapability(
        capability_id=TOOL_ID,
        execution_target=ExecutionTarget.LOCAL_TOOL,
        tool_name="echo",
        enabled=True,
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = capability
    tool_executor = AsyncMock()
    tool_executor.execute.return_value = StepResult(
        step_id="step_e", status=StepStatus.ERROR, error="tool said no"
    )

    uc = _uc(resolver, tool_executor)
    step = Step(id="step_e", execute_by=TOOL_ID)
    result = await uc.dispatch(_build_request(step))

    # No exception raised, so the tool's own ERROR result is returned verbatim.
    assert result.status == StepStatus.ERROR
    assert result.error == "tool said no"
