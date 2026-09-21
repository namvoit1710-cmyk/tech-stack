"""Delegated executor retryable classification + idempotency-gated timeout (SA-1716)."""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.step import (
    DispatchStepRequest,
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepStatus,
)
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter
from app.layer4_frameworks.services.technical_agent_step_executor import (
    TechnicalAgentStepExecutor,
)


class _Logger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


class _Delegator:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def delegate(self, _call):
        if self.fail:
            raise RuntimeError("send failed")


def _request():
    step = Step(id="s1", execute_by="agent.abc", input_data={"a": 1})
    return DispatchStepRequest(
        step=step, message_id="m", correlation_id="c", conv_id="cv",
        batch_id="b", from_agent="agent.business",
    )


_CAP = ResolvedCapability(
    capability_id="cap", execution_target=ExecutionTarget.TECHNICAL_AGENT, agent_type="abc"
)


@pytest.mark.asyncio
async def test_delegation_send_failure_is_retryable():
    ex = TechnicalAgentStepExecutor(
        delegator=_Delegator(fail=True), reply_waiter=AgentReplyWaiter(),
        reply_topic="t", timeout_seconds=1.0, logger=_Logger(),
    )
    result = await ex.execute(_request(), _CAP)
    assert result.status == StepStatus.ERROR
    assert result.retryable is True


@pytest.mark.asyncio
async def test_delegated_timeout_not_retryable_by_default():
    ex = TechnicalAgentStepExecutor(
        delegator=_Delegator(), reply_waiter=AgentReplyWaiter(),
        reply_topic="t", timeout_seconds=0.01, logger=_Logger(),
        retry_delegated_on_timeout=False,
    )
    result = await ex.execute(_request(), _CAP)  # nobody resolves -> times out
    assert result.status == StepStatus.ERROR
    assert "timed out" in result.error
    assert result.retryable is False  # idempotency guard


@pytest.mark.asyncio
async def test_delegated_timeout_retryable_when_enabled():
    ex = TechnicalAgentStepExecutor(
        delegator=_Delegator(), reply_waiter=AgentReplyWaiter(),
        reply_topic="t", timeout_seconds=0.01, logger=_Logger(),
        retry_delegated_on_timeout=True,
    )
    result = await ex.execute(_request(), _CAP)
    assert result.status == StepStatus.ERROR
    assert "timed out" in result.error
    assert result.retryable is True
