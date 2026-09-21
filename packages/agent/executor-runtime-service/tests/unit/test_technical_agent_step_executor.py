from __future__ import annotations

import asyncio

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


class _FakeDelegator:
    def __init__(self, waiter: AgentReplyWaiter) -> None:
        self.waiter = waiter
        self.calls: list = []

    async def delegate(self, request):
        self.calls.append(request)
        assert request.correlation_id in self.waiter._pending


class _FakeLogger:
    def __init__(self) -> None:
        self.infos: list = []
        self.warnings: list = []
        self.errors: list = []
        self.debugs: list = []

    def info(self, message: str, *args, **kwargs) -> None:
        self.infos.append((message, kwargs))

    def warning(self, message: str, *args, **kwargs) -> None:
        self.warnings.append((message, kwargs))

    def error(self, message: str, *args, **kwargs) -> None:
        self.errors.append((message, kwargs))

    def debug(self, message: str, *args, **kwargs) -> None:
        self.debugs.append((message, kwargs))


def _build_request() -> DispatchStepRequest:
    step = Step(
        id="step_1",
        execute_by="agent.11111111-1111-4111-8111-111111111111",
        input_data={"workflow_id": "wf-1", "run_id": "run-1", "foo": "bar"},
        goal="Delegate work",
        message="hello",
        uploaded_file_ids=["file-1"],
    )
    return DispatchStepRequest(
        step=step,
        message_id="msg-1",
        correlation_id="corr-1",
        conv_id="conv-1",
        batch_id="batch-1",
        from_agent="agent.business",
    )


def _build_capability() -> ResolvedCapability:
    return ResolvedCapability(
        capability_id="11111111-1111-4111-8111-111111111111",
        execution_target=ExecutionTarget.TECHNICAL_AGENT,
        agent_type="file-agent",
        queue_metadata={
            "request_topic": "executor.request.echo",
            "reply_topic": "agent.reply.executor",
            "request_message_type": "custom.request.type",
            "response_message_type": "custom.response.type",
            "delivery_hints": {"request_message_type": "ignored.by.executor"},
            "request_topics": ["executor.request.echo"],
            "queue_names": ["simplemdg/event/core-2/executor.request.echo"],
        },
        enabled=True,
    )


@pytest.mark.asyncio
async def test_technical_agent_executor_builds_and_resolves_success_reply():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    logger = _FakeLogger()
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=1.0,
        logger=logger,
    )

    request = _build_request()
    capability = _build_capability()
    task = asyncio.create_task(executor.execute(request, capability))

    await asyncio.sleep(0)
    correlation_id = delegator.calls[0].correlation_id
    waiter.resolve(
        correlation_id,
        {
            "type": "agent.step.status",
            "message_id": "msg-1",
            "correlation_id": correlation_id,
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": request.step.id,
                    "status": "success",
                    "output_data": {"answer": 42},
                },
            },
        },
    )

    result = await task

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"answer": 42}
    assert delegator.calls[0].agent_id == "11111111-1111-4111-8111-111111111111"
    assert delegator.calls[0].agent_type == "file-agent"
    assert delegator.calls[0].reply_topic == "agent.reply.executor"
    assert delegator.calls[0].request_message_type == "custom.request.type"
    assert delegator.calls[0].response_message_type == "custom.response.type"
    assert delegator.calls[0].queue_metadata == capability.queue_metadata
    assert delegator.calls[0].metadata == {"uploaded_file_ids": ["file-1"]}
    message_overrides = delegator.calls[0].message_overrides
    assert message_overrides["message_id"] != request.message_id
    assert message_overrides == {
        "message_id": message_overrides["message_id"],
        "conv_id": "conv-1",
        "conversation_id": "conv-1",
        "payload": {
            "from": "executor.runtime",
            "to": "11111111-1111-4111-8111-111111111111",
            "type": "step",
            "step": {
                "id": "step_1",
                "execute_by": "agent.11111111-1111-4111-8111-111111111111",
                "goal": "Delegate work",
                "message": "hello",
                "uploaded_file_ids": ["file-1"],
                "input_data": {"workflow_id": "wf-1", "run_id": "run-1", "foo": "bar"},
                "output_data": {},
            },
        },
    }
    assert delegator.calls[0].context_snapshot["request"]["correlation_id"] == "corr-1"
    assert (
        delegator.calls[0].context_snapshot["executor_metadata"]["executor_conv_id"]
        == "conv-1"
    )
    assert (
        delegator.calls[0].context_snapshot["executor_metadata"]["executor_batch_id"]
        == "batch-1"
    )
    assert (
        delegator.calls[0].context_snapshot["executor_metadata"]["workflow_id"]
        == "wf-1"
    )
    assert any(message == "Sending step to technical agent" for message, _ in logger.infos)
    assert any(message == "Received technical agent reply" for message, _ in logger.infos)
    assert any(message == "Normalized technical agent reply" for message, _ in logger.infos)


@pytest.mark.asyncio
async def test_technical_agent_executor_builds_message_overrides_without_uploaded_files():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=1.0,
        logger=_FakeLogger(),
    )

    request = _build_request()
    request.step.uploaded_file_ids = []
    capability = _build_capability()
    task = asyncio.create_task(executor.execute(request, capability))

    await asyncio.sleep(0)
    correlation_id = delegator.calls[0].correlation_id
    waiter.resolve(
        correlation_id,
        {
            "type": "agent.step.status",
            "message_id": "msg-empty-files",
            "correlation_id": correlation_id,
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": request.step.id,
                    "status": "success",
                    "output_data": {"answer": 1},
                },
            },
        },
    )

    result = await task

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"answer": 1}
    assert delegator.calls[0].message_overrides["payload"]["step"]["uploaded_file_ids"] == []
    assert delegator.calls[0].message_overrides["payload"]["step"]["output_data"] == {}


@pytest.mark.asyncio
async def test_technical_agent_executor_rejects_interrupted_reply():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=1.0,
        logger=_FakeLogger(),
    )

    request = _build_request()
    capability = _build_capability()
    task = asyncio.create_task(executor.execute(request, capability))

    await asyncio.sleep(0)
    correlation_id = delegator.calls[0].correlation_id
    waiter.resolve(
        correlation_id,
        {
            "type": "agent.step.status",
            "message_id": "msg-interrupted",
            "correlation_id": correlation_id,
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": request.step.id,
                    "status": "interrupted",
                    "output_data": {},
                },
            },
        },
    )

    result = await task

    assert result.status == StepStatus.ERROR
    assert "non-terminal status 'interrupted'" in result.error


@pytest.mark.asyncio
async def test_technical_agent_executor_turns_error_reply_into_failure():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=1.0,
        logger=_FakeLogger(),
    )

    request = _build_request()
    capability = _build_capability()
    task = asyncio.create_task(executor.execute(request, capability))

    await asyncio.sleep(0)
    correlation_id = delegator.calls[0].correlation_id
    waiter.resolve(
        correlation_id,
        {
            "type": "agent.step.status",
            "message_id": "msg-2",
            "correlation_id": correlation_id,
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": request.step.id,
                    "status": "error",
                    "error": {"message": "boom"},
                    "output_data": {},
                },
            },
        },
    )

    result = await task

    assert result.status == StepStatus.ERROR
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_technical_agent_executor_accepts_flat_legacy_reply_fallback():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=1.0,
        logger=_FakeLogger(),
    )

    request = _build_request()
    capability = _build_capability()
    task = asyncio.create_task(executor.execute(request, capability))

    await asyncio.sleep(0)
    correlation_id = delegator.calls[0].correlation_id
    waiter.resolve(
        correlation_id,
        {
            "type": "agent.step.status",
            "message_id": "msg-flat",
            "correlation_id": correlation_id,
            "status": "success",
            "output_data": {"answer": 99},
        },
    )

    result = await task

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"answer": 99}


@pytest.mark.asyncio
async def test_technical_agent_executor_times_out():
    waiter = AgentReplyWaiter()
    delegator = _FakeDelegator(waiter)
    executor = TechnicalAgentStepExecutor(
        delegator=delegator,
        reply_waiter=waiter,
        reply_topic="agent.reply.executor",
        timeout_seconds=0.01,
        logger=_FakeLogger(),
    )

    result = await executor.execute(_build_request(), _build_capability())

    assert result.status == StepStatus.ERROR
    assert "timed out" in result.error
