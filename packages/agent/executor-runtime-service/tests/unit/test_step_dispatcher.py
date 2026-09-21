"""Tests for DispatchStepUseCase."""

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

TOOL_ECHO_ID = "tool.11111111-1111-4111-8111-111111111111"
TOOL_DISABLED_ID = "tool.22222222-2222-4222-8222-222222222222"
AGENT_FILE_ID = "agent.33333333-3333-4333-8333-333333333333"
BROKEN_TOOL_ID = "tool.44444444-4444-4444-8444-444444444444"
MISSING_CAPABILITY_ID = "tool.55555555-5555-4555-8555-555555555555"


def _build_request(step: Step) -> DispatchStepRequest:
    return DispatchStepRequest(
        step=step,
        message_id="msg_001",
        correlation_id="corr_001",
        conv_id="conv_001",
        batch_id="batch_001",
        from_agent="agent.business",
    )


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockCapabilityResolver:
    def __init__(self, capabilities: dict[str, ResolvedCapability | None]):
        self._capabilities = capabilities

    async def resolve(self, execute_by: str) -> ResolvedCapability | None:
        return self._capabilities.get(execute_by)


class MockToolExecutor:
    def __init__(self, results: dict[str, StepResult] | None = None):
        self._results = results or {}
        self.executed: list[tuple[str, Step]] = []

    async def execute(self, tool_name: str, step: Step) -> StepResult:
        self.executed.append((tool_name, step))
        if tool_name in self._results:
            return self._results[tool_name]
        return StepResult(
            step_id=step.id,
            status=StepStatus.SUCCESS,
            output={"echo": step.goal},
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


class MockTechnicalAgentStepExecutor:
    def __init__(self, result: StepResult | None = None):
        self.result = result or StepResult(
            step_id="step",
            status=StepStatus.SUCCESS,
            output={"delegated": True},
        )
        self.calls: list[tuple[DispatchStepRequest, ResolvedCapability]] = []

    async def execute(
        self,
        request: DispatchStepRequest,
        capability: ResolvedCapability,
    ) -> StepResult:
        self.calls.append((request, capability))
        return self.result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_local_tool_success():
    resolver = MockCapabilityResolver(
        {
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
        logger=MockLogger(),
    )

    step = Step(id="step_1", execute_by=TOOL_ECHO_ID, message="hello")
    result = await dispatcher.dispatch(_build_request(step))

    assert result.status == StepStatus.SUCCESS
    assert result.step_id == "step_1"
    assert len(tool_executor.executed) == 1
    assert tool_executor.executed[0][0] == "echo"


@pytest.mark.asyncio
async def test_dispatch_capability_not_found():
    resolver = MockCapabilityResolver({})
    tool_executor = MockToolExecutor()
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=MockLogger(),
    )

    step = Step(id="step_2", execute_by=MISSING_CAPABILITY_ID)
    result = await dispatcher.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert "not found" in result.error
    assert len(tool_executor.executed) == 0


@pytest.mark.asyncio
async def test_dispatch_capability_disabled():
    resolver = MockCapabilityResolver(
        {
            TOOL_DISABLED_ID: ResolvedCapability(
                capability_id=TOOL_DISABLED_ID,
                execution_target=ExecutionTarget.LOCAL_TOOL,
                tool_name="disabled",
                enabled=False,
            )
        }
    )
    tool_executor = MockToolExecutor()
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=MockLogger(),
    )

    step = Step(id="step_3", execute_by=TOOL_DISABLED_ID)
    result = await dispatcher.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert "disabled" in result.error


@pytest.mark.asyncio
async def test_dispatch_technical_agent_returns_error_in_mvp():
    resolver = MockCapabilityResolver(
        {
            AGENT_FILE_ID: ResolvedCapability(
                capability_id=AGENT_FILE_ID,
                execution_target=ExecutionTarget.TECHNICAL_AGENT,
                agent_type="file-agent",
                enabled=True,
            )
        }
    )
    tool_executor = MockToolExecutor()
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=MockLogger(),
    )

    step = Step(id="step_4", execute_by=AGENT_FILE_ID, message="process file")
    result = await dispatcher.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert "not yet implemented" in result.error


@pytest.mark.asyncio
async def test_dispatch_technical_agent_uses_injected_executor():
    resolver = MockCapabilityResolver(
        {
            AGENT_FILE_ID: ResolvedCapability(
                capability_id=AGENT_FILE_ID,
                execution_target=ExecutionTarget.TECHNICAL_AGENT,
                agent_type="file-agent",
                enabled=True,
            )
        }
    )
    technical_executor = MockTechnicalAgentStepExecutor(
        StepResult(step_id="step_4", status=StepStatus.SUCCESS, output={"done": True})
    )
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=MockToolExecutor(),
        technical_agent_step_executor=technical_executor,
        logger=MockLogger(),
    )

    step = Step(id="step_4", execute_by=AGENT_FILE_ID, message="process file")
    request = _build_request(step)
    result = await dispatcher.dispatch(request)

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"done": True}
    assert len(technical_executor.calls) == 1
    assert technical_executor.calls[0][0] == request
    assert technical_executor.calls[0][1].agent_type == "file-agent"


@pytest.mark.asyncio
async def test_dispatch_local_tool_no_tool_name():
    resolver = MockCapabilityResolver(
        {
            BROKEN_TOOL_ID: ResolvedCapability(
                capability_id=BROKEN_TOOL_ID,
                execution_target=ExecutionTarget.LOCAL_TOOL,
                tool_name=None,  # Missing tool_name
                enabled=True,
            )
        }
    )
    tool_executor = MockToolExecutor()
    dispatcher = DispatchStepUseCase(
        capability_resolver=resolver,
        tool_executor=tool_executor,
        logger=MockLogger(),
    )

    step = Step(id="step_5", execute_by=BROKEN_TOOL_ID)
    result = await dispatcher.dispatch(_build_request(step))

    assert result.status == StepStatus.ERROR
    assert "no tool_name" in result.error
