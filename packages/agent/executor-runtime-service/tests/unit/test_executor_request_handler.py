"""Tests for ExecutorQueueHandler."""

import pytest

from app.layer1_domain.entities.step import (
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
)
from app.layer2_application.queues.executor_queue_handler import (
    ExecutorQueueHandler,
    StepStatusPublisherInput,
)
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)

TOOL_ECHO_ID = "tool.11111111-1111-4111-8111-111111111111"
TOOL_SEARCH_ID = "tool.22222222-2222-4222-8222-222222222222"
UNKNOWN_TOOL_ID = "tool.33333333-3333-4333-8333-333333333333"


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockCapabilityResolver:
    def __init__(self, capabilities: dict[str, ResolvedCapability | None]):
        self._capabilities = capabilities

    async def resolve(self, execute_by: str) -> ResolvedCapability | None:
        return self._capabilities.get(execute_by)


class MockToolExecutor:
    async def execute(self, tool_name: str, step: Step) -> StepResult:
        return StepResult(
            step_id=step.id,
            status=StepStatus.SUCCESS,
            output={"result": f"echo: {step.input_data.get('input_text', '')}"},
        )


class MockStatusPublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish_status(
        self,
        input: StepStatusPublisherInput,
    ) -> None:
        self.published.append(
            {
                "step_id": input.step.id,
                "status": input.step.status.value if input.step.status else None,
                "message": input.step.message,
                "error": input.step.error,
                "to_agent": input.to_agent,
                "conv_id": input.conv_id,
                "correlation_id": input.correlation_id,
            }
        )


class MockLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict]] = []
        self.warnings: list[tuple[str, dict]] = []
        self.errors: list[tuple[str, dict]] = []
        self.debugs: list[tuple[str, dict]] = []

    def info(self, message: str, *args, **kwargs) -> None:
        self.infos.append((message, kwargs))

    def warning(self, message: str, *args, **kwargs) -> None:
        self.warnings.append((message, kwargs))

    def error(self, message: str, *args, **kwargs) -> None:
        self.errors.append((message, kwargs))

    def debug(self, message: str, *args, **kwargs) -> None:
        self.debugs.append((message, kwargs))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _build_handler(capabilities=None):
    dispatcher_logger = MockLogger()
    handler_logger = MockLogger()
    resolver = MockCapabilityResolver(
        capabilities
        or {
            TOOL_ECHO_ID: ResolvedCapability(
                capability_id=TOOL_ECHO_ID,
                execution_target=ExecutionTarget.LOCAL_TOOL,
                tool_name="echo",
                enabled=True,
            )
        }
    )
    tool_executor = MockToolExecutor()
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=dispatcher_logger,
    )
    status_publisher = MockStatusPublisher()
    handler = ExecutorQueueHandler(
        dispatcher=dispatcher,
        status_publisher=status_publisher,
        logger=handler_logger,
    )
    return handler, status_publisher, handler_logger, dispatcher_logger


@pytest.mark.asyncio
async def test_handle_valid_single_step():
    handler, publisher, handler_logger, _dispatcher_logger = _build_handler()

    payload = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_001",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": TOOL_ECHO_ID,
                    "input_data": {"input_text": "hello world"},
                    "output_data": {},
                    "goal": "hello world",
                    "message": "",
                }
            ],
        },
    }

    await handler.handle_step_batch(payload)

    # Should publish: processing, then success
    assert len(publisher.published) == 2
    assert publisher.published[0]["status"] == "processing"
    assert publisher.published[0]["step_id"] == "step_1"
    assert publisher.published[0]["error"] is None
    assert publisher.published[0]["to_agent"] == "agent.business"
    assert publisher.published[1]["status"] == "success"
    assert publisher.published[1]["step_id"] == "step_1"
    assert publisher.published[1]["error"] is None
    assert publisher.published[1]["to_agent"] == "agent.business"
    assert any(message == "Received step for execution" for message, _ in handler_logger.infos)
    terminal_logs = [
        kwargs
        for message, kwargs in handler_logger.infos
        if message == "Publishing step result"
    ]
    assert terminal_logs
    assert terminal_logs[0]["input_data"] == {"input_text": "hello world"}
    assert terminal_logs[0]["output_data"] == {"result": "echo: hello world"}


@pytest.mark.asyncio
async def test_handle_validation_failure_publishes_error():
    handler, publisher, _handler_logger, _dispatcher_logger = _build_handler()

    # Missing message_id — validation will fail
    payload = {
        "type": "agent.request.executor",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "step_1", "execute_by": TOOL_ECHO_ID, "message": "hello"}],
        },
    }

    await handler.handle_step_batch(payload)

    # Should publish error for the step
    assert len(publisher.published) == 1
    assert publisher.published[0]["status"] == "error"
    assert "Validation failed" in publisher.published[0]["error"]


@pytest.mark.asyncio
async def test_handle_multi_step_batch():
    handler, publisher, _handler_logger, _dispatcher_logger = _build_handler(
        {
            TOOL_ECHO_ID: ResolvedCapability(
                capability_id=TOOL_ECHO_ID,
                execution_target=ExecutionTarget.LOCAL_TOOL,
                tool_name="echo",
                enabled=True,
            ),
            TOOL_SEARCH_ID: ResolvedCapability(
                capability_id=TOOL_SEARCH_ID,
                execution_target=ExecutionTarget.LOCAL_TOOL,
                tool_name="search",
                enabled=True,
            ),
        }
    )

    payload = {
        "type": "agent.request.executor",
        "message_id": "msg_002",
        "correlation_id": "corr_002",
        "conv_id": "conv_2",
        "payload": {
            "from": "agent.analytics",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_002",
            "steps": [
                {
                    "id": "step_a",
                    "execute_by": TOOL_ECHO_ID,
                    "input_data": {"input_text": "a"},
                    "output_data": {},
                    "message": "a",
                },
                {
                    "id": "step_b",
                    "execute_by": TOOL_SEARCH_ID,
                    "input_data": {"input_text": "b"},
                    "output_data": {},
                    "message": "b",
                },
            ],
        },
    }

    await handler.handle_step_batch(payload)

    # 2 steps × 2 events (processing + success) = 4 events
    assert len(publisher.published) == 4
    assert publisher.published[0]["status"] == "processing"
    assert publisher.published[0]["step_id"] == "step_a"
    assert publisher.published[1]["status"] == "success"
    assert publisher.published[1]["step_id"] == "step_a"
    assert publisher.published[2]["status"] == "processing"
    assert publisher.published[2]["step_id"] == "step_b"
    assert publisher.published[3]["status"] == "success"
    assert publisher.published[3]["step_id"] == "step_b"


@pytest.mark.asyncio
async def test_handle_dispatch_failure_publishes_error():
    handler, publisher, _handler_logger, _dispatcher_logger = _build_handler(
        {
            # Capability not found for this execute_by
        }
    )

    payload = {
        "type": "agent.request.executor",
        "message_id": "msg_003",
        "correlation_id": "corr_003",
        "conv_id": "conv_3",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [
                {"id": "step_x", "execute_by": UNKNOWN_TOOL_ID, "message": "fail"}
            ],
        },
    }

    await handler.handle_step_batch(payload)

    # processing then error
    assert len(publisher.published) == 2
    assert publisher.published[0]["status"] == "processing"
    assert publisher.published[1]["status"] == "error"
    assert "not found" in publisher.published[1]["error"]
