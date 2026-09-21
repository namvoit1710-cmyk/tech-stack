"""SA-892 (task-scoped stop): business-agent PRIMARY per-task stop guard.

Repro: a continuation event (CONVERSATION_PLAN_CREATED — the self-published
"next turn" signal) whose OWN task was cancelled by a Stop (Task.status ==
CANCELLED) must be DROPPED at turn entry: the plan/graph is not invoked, no next
task is handed off, nothing is published. Without the guard the continuation is
routed and processed (resurrecting the stopped message) — the failing repro.

Contrast (pre-change / new-message behavior): a continuation whose task is
PROCESSING or PENDING — e.g. a fresh user message's task, which a Stop never
cancels because it carries a different message_id — is routed as normal.

The guard resolves the event's task id from the event payload's step id when
present, else from Metadata.current_task_id (the link the CONVERSATION_PLAN_CREATED
continuation uses), then looks that task up in the conversation's executions.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    Metadata,
    Task,
    TaskStatus,
)
from app.layer1_domain.entities.queue_event import QueueRequestType
from app.layer2_application.features.main_agent.dto.event import EventDTO
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
)


def _metadata(conv_id: str = "conv-1", current_task_id: str = "task-1") -> Metadata:
    return Metadata(
        conv_id=conv_id,
        checkpoint_id="CP-01",
        current_task_id=current_task_id,
    )


def _task(status: TaskStatus, task_id: str = "task-1") -> Task:
    return Task(
        task_id=task_id,
        agent="agent.file",
        name="do a thing",
        status=status,
    )


def _event(conv_id: str = "conv-1") -> EventDTO:
    # CONVERSATION_PLAN_CREATED is the self-published continuation: it carries NO
    # payload step id, so the guard resolves the task via Metadata.current_task_id.
    return EventDTO(
        id="event-1",
        type=QueueRequestType.CONVERSATION_PLAN_CREATED,
        conv_id=conv_id,
        correlation_id="corr-1",
        payload=None,
        metadata={"source": "test"},
    )


def _usecase(task_status: TaskStatus):
    repository = SimpleNamespace(
        get_metadata=Mock(return_value=_metadata()),
        get_task=Mock(return_value=_task(task_status)),
        get_conversation_id_by_run_id=Mock(return_value="conv-1"),
    )
    publisher = SimpleNamespace(publish=AsyncMock())
    logger = SimpleNamespace(
        info=Mock(), warning=Mock(), error=Mock(), debug=Mock()
    )
    subject = CreatePlanUseCase(
        planner_resolver=SimpleNamespace(resolve=Mock()),
        agent_hub_client=SimpleNamespace(),
        agent_shared_state_repository=repository,
        workflow_client=SimpleNamespace(),
        push_gateway_notifier=SimpleNamespace(send_task_summary_event=AsyncMock()),
        logger=logger,
        publisher=publisher,
        uuid_builder=SimpleNamespace(create_uuid7=Mock(return_value="uuid-1")),
        settings=SimpleNamespace(),
        file_client=SimpleNamespace(),
    )
    return subject, repository, publisher


@pytest.mark.asyncio
async def test_cancelled_task_continuation_is_dropped():
    """The key repro: a continuation whose task is CANCELLED is dropped — the
    plan case handler is never invoked and nothing is published."""
    subject, repository, publisher = _usecase(TaskStatus.CANCELLED)
    handler = AsyncMock()
    subject._execute_conversation_plan_created = handler  # type: ignore[method-assign]

    await subject.execute(_event())

    handler.assert_not_awaited()  # turn never starts
    publisher.publish.assert_not_awaited()  # no follow-up dispatch
    repository.get_task.assert_called_with("conv-1", "task-1")


@pytest.mark.asyncio
async def test_processing_task_continuation_is_processed():
    """Contrast: a continuation whose task is PROCESSING (a live turn / a new
    message's task, which a Stop never cancels) is routed normally."""
    subject, _repository, _publisher = _usecase(TaskStatus.PROCESSING)
    handler = AsyncMock()
    subject._execute_conversation_plan_created = handler  # type: ignore[method-assign]

    await subject.execute(_event())

    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_task_continuation_is_processed():
    """Contrast: a PENDING task (e.g. a fresh user message's first task) processes."""
    subject, _repository, _publisher = _usecase(TaskStatus.PENDING)
    handler = AsyncMock()
    subject._execute_conversation_plan_created = handler  # type: ignore[method-assign]

    await subject.execute(_event())

    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_guard_true_for_cancelled_task():
    subject, _repository, _publisher = _usecase(TaskStatus.CANCELLED)
    assert subject._incoming_task_is_cancelled(_event()) is True


@pytest.mark.asyncio
async def test_guard_false_for_processing_task():
    subject, _repository, _publisher = _usecase(TaskStatus.PROCESSING)
    assert subject._incoming_task_is_cancelled(_event()) is False


@pytest.mark.asyncio
async def test_guard_falls_through_when_task_unresolved():
    """Best-effort: an unresolved task (get_task -> None) must NOT drop a legit
    turn — the guard returns False so processing continues."""
    subject, repository, _publisher = _usecase(TaskStatus.CANCELLED)
    repository.get_task = Mock(return_value=None)
    assert subject._incoming_task_is_cancelled(_event()) is False


@pytest.mark.asyncio
async def test_guard_prefers_payload_step_id_over_current_task_id():
    """When the event payload carries a step id it is used to resolve the task
    (a step-result callback), in preference to Metadata.current_task_id."""
    subject, repository, _publisher = _usecase(TaskStatus.CANCELLED)
    event = EventDTO(
        id="event-2",
        type=QueueRequestType.EXECUTOR_STEP_STATUS,
        conv_id="conv-1",
        correlation_id="corr-2",
        payload=SimpleNamespace(steps=[SimpleNamespace(id="step-9")]),
        metadata=None,
    )

    assert subject._incoming_task_is_cancelled(event) is True
    repository.get_task.assert_called_with("conv-1", "step-9")
    # current_task_id fallback was not needed.
    repository.get_metadata.assert_not_called()
