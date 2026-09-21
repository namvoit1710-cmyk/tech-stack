"""SA-1974: _handle_direct_answer passes next-step suggestions to send_task_summary_event."""
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from agent_sdk import settings
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    Metadata,
    Task,
    TaskStatus,
)
from app.layer1_domain.entities.next_step_suggestion import (
    NextStepKind,
    NextStepSuggestion,
)
from app.layer1_domain.entities.planner import PlannerResponse, PlannerResponseType
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _metadata(conv_id: str = "conv-1") -> Metadata:
    return Metadata(
        conv_id=conv_id,
        checkpoint_id="cp-1",
        current_task_id="task-1",
        current_message_id="msg-1",
    )


def _task(*, next_task_id: str = "") -> Task:
    return Task.model_validate({
        "task_id": "task-1",
        "agent": "agent.biz",
        "name": "answer question",
        "goal": "What is revenue?",
        "status": TaskStatus.PROCESSING,
        "execute_by": "agent.biz",
        "next_task_id": next_task_id,
        "depend_on": [],
        "is_dynamic": False,
        "input_data": None,
        "output_data": None,
        "error_detail": None,
        "workflow_id": None,
        "agent_id": "biz",
        "parent_task_id": None,
    })


def _suggestion(title: str = "Show details") -> NextStepSuggestion:
    return NextStepSuggestion(
        title=title,
        prompt=f"Please show me {title.lower()}",
        kind=NextStepKind.FOLLOW_UP,
        suggestion_id="sg-1",
    )


def _direct_answer_plan(
    answer: str = "The revenue is $100",
    suggestions: list[NextStepSuggestion] | None = None,
) -> PlannerResponse:
    return PlannerResponse(
        type=PlannerResponseType.DIRECT_ANSWER,
        answer=answer,
        suggestions=suggestions or [],
    )


def _usecase(
    *,
    push_gateway_notifier: Any = None,
    agent_shared_state_repository: Any = None,
) -> CreatePlanUseCase:
    push_gateway_notifier = push_gateway_notifier or SimpleNamespace(
        send_task_summary_event=AsyncMock(),
    )
    agent_shared_state_repository = agent_shared_state_repository or SimpleNamespace(
        upsert_executions=Mock(),
        update_task=Mock(return_value=_task()),
        update_metadata=Mock(),
    )
    return CreatePlanUseCase(
        planner_resolver=cast(Any, SimpleNamespace(resolve=Mock())),
        agent_hub_client=cast(Any, SimpleNamespace()),
        agent_shared_state_repository=cast(Any, agent_shared_state_repository),
        workflow_client=cast(Any, SimpleNamespace()),
        push_gateway_notifier=cast(Any, push_gateway_notifier),
        logger=cast(Any, SimpleNamespace(info=Mock(), error=Mock(), warning=Mock(), debug=Mock())),
        publisher=cast(Any, SimpleNamespace(publish=AsyncMock())),
        uuid_builder=cast(Any, SimpleNamespace(create_uuid7=Mock(return_value="uuid-1"))),
        settings=settings,
        file_client=cast(Any, SimpleNamespace()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_direct_answer_passes_suggestions_when_present() -> None:
    suggestions: list[NextStepSuggestion] = [
        _suggestion("Show details"),
        _suggestion("Export report"),
    ]
    notifier: Any = SimpleNamespace(send_task_summary_event=AsyncMock())
    subject: CreatePlanUseCase = _usecase(push_gateway_notifier=notifier)

    await subject._handle_direct_answer(
        "conv-1",
        _metadata(),
        _task(),
        _direct_answer_plan(suggestions=suggestions),
    )

    notifier.send_task_summary_event.assert_awaited_once_with(
        conv_id="conv-1",
        summary="The revenue is $100",
        suggestions=suggestions,
    )


@pytest.mark.asyncio
async def test_handle_direct_answer_passes_empty_suggestions_when_none_returned() -> None:
    notifier: Any = SimpleNamespace(send_task_summary_event=AsyncMock())
    subject: CreatePlanUseCase = _usecase(push_gateway_notifier=notifier)

    await subject._handle_direct_answer(
        "conv-1",
        _metadata(),
        _task(),
        _direct_answer_plan(suggestions=[]),
    )

    notifier.send_task_summary_event.assert_awaited_once_with(
        conv_id="conv-1",
        summary="The revenue is $100",
        suggestions=[],
    )


@pytest.mark.asyncio
async def test_handle_direct_answer_raises_when_answer_is_none() -> None:
    subject: CreatePlanUseCase = _usecase()

    with pytest.raises(ValueError, match="plan.answer is required"):
        await subject._handle_direct_answer(
            "conv-1",
            _metadata(),
            _task(),
            PlannerResponse(type=PlannerResponseType.DIRECT_ANSWER, answer=None),
        )
