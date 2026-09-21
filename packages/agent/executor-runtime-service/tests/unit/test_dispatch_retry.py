"""DispatchStepUseCase integrates RetryPolicy end-to-end (SA-1716)."""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.step import (
    DispatchStepRequest,
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
)
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)
from app.layer2_application.services.retry_policy import RetryPolicy


class _Logger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


class _Resolver:
    def __init__(self, cap):
        self._cap = cap

    async def resolve(self, _execute_by):
        return self._cap


class _ToolExec:
    def __init__(self, results):
        self._results = results
        self.calls = 0

    async def execute(self, _tool_name, _step):
        r = self._results[self.calls]
        self.calls += 1
        return r


async def _nosleep(_seconds):
    return None


def _request():
    step = Step(id="s1", execute_by="tool.x", input_data={})
    return DispatchStepRequest(
        step=step, message_id="m", correlation_id="c", conv_id="cv",
        batch_id="b", from_agent="agent.business",
    )


_LOCAL_CAP = ResolvedCapability(
    capability_id="cap", execution_target=ExecutionTarget.LOCAL_TOOL, tool_name="t", enabled=True
)


def _transient():
    return StepResult(step_id="s1", status=StepStatus.ERROR, error="glitch", retryable=True)


def _ok():
    return StepResult(step_id="s1", status=StepStatus.SUCCESS, output={"r": 1})


@pytest.mark.asyncio
async def test_dispatch_retries_transient_then_succeeds():
    tool = _ToolExec([_transient(), _transient(), _ok()])
    policy = RetryPolicy(max_attempts=3, jitter=False, sleep=_nosleep)
    uc = DispatchStepUseCase(
        capability_resolver=_Resolver(_LOCAL_CAP),
        tool_executor=tool,
        logger=_Logger(),
        retry_policy=policy,
    )
    result = await uc.dispatch(_request())
    assert result.status == StepStatus.SUCCESS
    assert tool.calls == 3  # retried twice, succeeded on the third


@pytest.mark.asyncio
async def test_dispatch_capability_not_found_is_terminal_single_attempt():
    tool = _ToolExec([_ok()])  # must never be called
    policy = RetryPolicy(max_attempts=3, jitter=False, sleep=_nosleep)
    uc = DispatchStepUseCase(
        capability_resolver=_Resolver(None),
        tool_executor=tool,
        logger=_Logger(),
        retry_policy=policy,
    )
    result = await uc.dispatch(_request())
    assert result.status == StepStatus.ERROR
    assert result.retryable is False
    assert "Capability not found" in result.error
    assert tool.calls == 0  # resolution failure is not retried


@pytest.mark.asyncio
async def test_dispatch_budget_exhausted_returns_terminal():
    tool = _ToolExec([_transient(), _transient(), _transient()])
    policy = RetryPolicy(max_attempts=3, jitter=False, sleep=_nosleep)
    uc = DispatchStepUseCase(
        capability_resolver=_Resolver(_LOCAL_CAP),
        tool_executor=tool,
        logger=_Logger(),
        retry_policy=policy,
    )
    result = await uc.dispatch(_request())
    assert result.status == StepStatus.ERROR
    assert result.retryable is False
    assert "Retry budget exhausted" in result.error
    assert tool.calls == 3
