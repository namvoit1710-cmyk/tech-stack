from __future__ import annotations

import pytest

from app.layer1_domain.entities.step import (
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)
from app.layer2_application.queues.executor_queue_handler import ExecutorQueueHandler
from app.layer4_frameworks.dependency.ioc_container import build_status_publisher

TOOL_ECHO_ID = "tool.b85063e7-32c8-46c9-90e2-0c836b1d6fa8"


class _IntegrationCapabilityResolver:
    async def resolve(self, execute_by: str) -> ResolvedCapability | None:
        if execute_by != TOOL_ECHO_ID:
            return None
        return ResolvedCapability(
            capability_id=TOOL_ECHO_ID,
            execution_target=ExecutionTarget.LOCAL_TOOL,
            tool_name="echo",
            enabled=True,
        )


class _IntegrationToolExecutor:
    async def execute(self, tool_name: str, step: Step) -> StepResult:
        assert tool_name == "echo"
        return StepResult(
            step_id=step.id,
            status=StepStatus.SUCCESS,
            output={"result": f"echo: {step.input_data.get('input_text', '')}"},
        )


class _IntegrationLogger:
    def info(self, message: str, *args, **kwargs) -> None:
        return None

    def warning(self, message: str, *args, **kwargs) -> None:
        return None

    def error(self, message: str, *args, **kwargs) -> None:
        return None

    def debug(self, message: str, *args, **kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_executor_integration_bootstraps_and_processes_step_batch(
    bootstrap_module,
    executor_publisher,
    executor_step_batch_payload,
):
    assert bootstrap_module.settings.APP_NAME == "Executor Runtime Service"

    dispatcher = DispatchStepUseCase(
        capability_resolver=_IntegrationCapabilityResolver(),
        tool_executor=_IntegrationToolExecutor(),
        logger=_IntegrationLogger(),
    )
    handler = ExecutorQueueHandler(
        dispatcher=dispatcher,
        status_publisher=build_status_publisher(executor_publisher),
        logger=_IntegrationLogger(),
    )

    await handler.handle_step_batch(executor_step_batch_payload)

    assert len(executor_publisher.calls) == 2
    assert executor_publisher.calls[0]["topic"] == settings.STATUS_PUBLISH_TOPIC
    assert (
        executor_publisher.calls[0]["message"]["payload"]["steps"][0]["status"]
        == "processing"
    )
    assert (
        executor_publisher.calls[1]["message"]["payload"]["steps"][0]["status"]
        == "success"
    )
    assert (
        executor_publisher.calls[1]["message"]["payload"]["steps"][0]["message"] == ""
    )
    assert executor_publisher.calls[1]["message"]["payload"]["steps"][0][
        "output_data"
    ] == {"result": "echo: Hello from integration"}
