from types import SimpleNamespace

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
    Task,
)
from app.layer1_domain.entities.agent_hub import ToolHubDTO
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
)


class DummyLogger:
    def info(self, message: str, **kwargs) -> None:
        pass

    def error(self, message: str, **kwargs) -> None:
        pass

    def warning(self, message: str, **kwargs) -> None:
        pass

    def debug(self, message: str, **kwargs) -> None:
        pass


def _metadata() -> Metadata:
    return Metadata(conv_id="conv-1", checkpoint_id="checkpoint-1")


def _conversation(user_id: str = "user@example.com") -> Conversation:
    metadata = _metadata()
    return Conversation(
        conv_id="conv-1",
        user_id=user_id,
        shared_state=AgentSharedState(
            metadata=metadata,
            context=GlobalContext(conversation_context=[], knowledge_base_id=""),
            executions=[],
        ),
    )


def _task(**updates) -> Task:
    payload = {
        "task_id": "task-1",
        "agent": "tool.tool-1",
        "name": "Run workflow",
        "agent_id": "agent-1",
        "input_data": {},
        "output_data": {},
    }
    payload.update(updates)
    return Task(**payload)


def _usecase(repository, agent_hub_client, push_gateway_notifier, uuid_builder) -> CreatePlanUseCase:
    return CreatePlanUseCase(
        planner_resolver=SimpleNamespace(),
        agent_hub_client=agent_hub_client,
        agent_shared_state_repository=repository,
        workflow_client=SimpleNamespace(),
        push_gateway_notifier=push_gateway_notifier,
        logger=DummyLogger(),
        publisher=SimpleNamespace(),
        uuid_builder=uuid_builder,
        settings=SimpleNamespace(EXECUTOR_TOPIC="executor", BUSINESS_TOPIC="business"),
        file_client=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_pre_check_step_merges_user_email_into_workflow_input_data():
    metadata = _metadata()
    repository = SimpleNamespace(
        get_conversation=lambda conv_id: _conversation(),
        get_metadata=lambda conv_id: metadata,
        update_metadata=lambda conv_id, updated_metadata: None,
    )
    uuid_builder = SimpleNamespace(create_uuid7=lambda: "run-123")
    subject = _usecase(
        repository=repository,
        agent_hub_client=SimpleNamespace(
            get_tools_by_id=_async_return(
                ToolHubDTO(
                    id="tool-1",
                    name="workflow_start_run",
                    description="Start workflow",
                    input_data={},
                    output_data={},
                )
            )
        ),
        push_gateway_notifier=SimpleNamespace(
            send_thinking_event=_async_return(None)
        ),
        uuid_builder=uuid_builder,
    )
    execution_plans = [
        _task(input_data={"input_data": {"existing": "value"}}),
    ]

    await subject.pre_check_step("conv-1", execution_plans)

    assert execution_plans[0].input_data == {
        "predefined_run_id": "run-123",
        "input_data": {
            "existing": "value",
            "user_email": "user@example.com",
        },
    }
    assert metadata.current_run_id == "run-123"


@pytest.mark.asyncio
async def test_pre_check_step_skips_non_workflow_tools():
    metadata = _metadata()
    subject = _usecase(
        repository=SimpleNamespace(
            get_conversation=lambda conv_id: _conversation(),
            get_metadata=lambda conv_id: metadata,
            update_metadata=lambda conv_id, updated_metadata: None,
        ),
        agent_hub_client=SimpleNamespace(
            get_tools_by_id=_async_return(
                ToolHubDTO(
                    id="tool-1",
                    name="plain_tool",
                    description="Regular tool",
                    input_data={},
                    output_data={},
                )
            )
        ),
        push_gateway_notifier=SimpleNamespace(
            send_thinking_event=_async_return(None)
        ),
        uuid_builder=SimpleNamespace(create_uuid7=lambda: "run-123"),
    )
    execution_plans = [_task(input_data={"message": "hi"})]

    await subject.pre_check_step("conv-1", execution_plans)

    assert execution_plans[0].input_data == {"message": "hi"}
    assert metadata.current_run_id is None


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner
