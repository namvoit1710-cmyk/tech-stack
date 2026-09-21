"""SA-892 (task-scoped stop) M: drop a STALE self-published continuation.

A ``CONVERSATION_PLAN_CREATED`` continuation carries no payload step id, so the
stop guard resolves its task via ``Metadata.current_task_id``. A NEWER user
message can advance ``current_task_id`` to ITS OWN task before the stale
continuation is processed — so the continuation would resolve to a fresh
(non-cancelled) task and double-process it.

The self-published continuation carries its origin message id and, when the guard
falls back to ``current_task_id``, drops the continuation as STALE if that origin
disagrees with the resolved task's origin message — reverse-looked-up from
``conversation_context`` (message.tasks), NOT a field on the Task.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
    Task,
    TaskStatus,
)
from agent_sdk.layer1_domain.entities.conversation_context import (
    MessageHistory,
    TaskHistory,
)
from app.layer1_domain.entities.queue_event import QueueRequestType
from app.layer2_application.features.main_agent.dto.event import EventDTO
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
)


NEW_TASK_ID = "task-new"
NEW_MESSAGE_ID = "msg-new"
OLD_MESSAGE_ID = "msg-old"


def _metadata() -> Metadata:
    # current_task_id has already advanced to the NEW message's task.
    return Metadata(
        conv_id="conv-1",
        checkpoint_id="CP-01",
        current_task_id=NEW_TASK_ID,
        current_message_id=NEW_MESSAGE_ID,
    )


def _new_task(status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(
        task_id=NEW_TASK_ID,
        agent="agent.file",
        name="new message task",
        status=status,
    )


def _context() -> GlobalContext:
    # conversation_context is the task→message index: the NEW task is registered
    # under the NEW message (the reverse-lookup source for the staleness check).
    return GlobalContext(
        conversation_context=[
            MessageHistory(
                message_id=NEW_MESSAGE_ID,
                message="new message",
                tasks=[TaskHistory(task_id=NEW_TASK_ID, knowledge=[])],
            )
        ]
    )


def _continuation(origin_message_id: str | None) -> EventDTO:
    return EventDTO(
        id="event-1",
        type=QueueRequestType.CONVERSATION_PLAN_CREATED,
        conv_id="conv-1",
        correlation_id="corr-1",
        payload=None,
        metadata={"source": "test"},
        origin_message_id=origin_message_id,
    )


def _usecase():
    publisher = SimpleNamespace(publish=AsyncMock())
    repository = SimpleNamespace(
        get_metadata=Mock(return_value=_metadata()),
        get_task=Mock(return_value=_new_task()),
        get_context_by_conv_id=Mock(return_value=_context()),
        update_task=Mock(),
        get_conversation_id_by_run_id=Mock(return_value="conv-1"),
    )
    logger = SimpleNamespace(info=Mock(), warning=Mock(), error=Mock(), debug=Mock())
    push = SimpleNamespace(
        send_chat_disable_event=AsyncMock(),
        send_thinking_event=AsyncMock(),
        send_task_summary_event=AsyncMock(),
    )
    subject = CreatePlanUseCase(
        planner_resolver=SimpleNamespace(resolve=Mock()),
        agent_hub_client=SimpleNamespace(),
        agent_shared_state_repository=repository,
        workflow_client=SimpleNamespace(),
        push_gateway_notifier=push,
        logger=logger,
        publisher=publisher,
        uuid_builder=SimpleNamespace(create_uuid7=Mock(return_value="uuid-1")),
        settings=SimpleNamespace(),
        file_client=SimpleNamespace(),
    )
    return subject, repository, publisher


# ── guard-level ──


def test_guard_drops_stale_continuation():
    """origin (old) != resolved task's message_id (new) -> STALE -> drop."""
    subject, _repo, _pub = _usecase()
    assert subject._incoming_task_is_cancelled(_continuation(OLD_MESSAGE_ID)) is True


def test_guard_processes_current_continuation():
    """origin == resolved task's message_id -> not stale -> processed (task PENDING)."""
    subject, _repo, _pub = _usecase()
    assert subject._incoming_task_is_cancelled(_continuation(NEW_MESSAGE_ID)) is False


def test_guard_falls_through_when_no_origin_message_id():
    """Older continuation shape (no origin id) keeps today's behavior: cannot prove
    staleness, so it falls through to the cancelled-check (task PENDING -> process)."""
    subject, _repo, _pub = _usecase()
    assert subject._incoming_task_is_cancelled(_continuation(None)) is False


# ── execute() end-to-end ──


@pytest.mark.asyncio
async def test_stale_continuation_is_dropped_end_to_end():
    """The stale continuation must not invoke the plan handler nor publish.

    Pre-fix (guard ignored origin message id) it resolved to the NEW task, was
    NOT cancelled, and got processed — double-processing the new message's task."""
    subject, _repo, publisher = _usecase()
    handler = AsyncMock()
    subject._execute_conversation_plan_created = handler  # type: ignore[method-assign]

    await subject.execute(_continuation(OLD_MESSAGE_ID))

    handler.assert_not_awaited()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_continuation_is_processed_end_to_end():
    subject, _repo, _pub = _usecase()
    handler = AsyncMock()
    subject._execute_conversation_plan_created = handler  # type: ignore[method-assign]

    await subject.execute(_continuation(NEW_MESSAGE_ID))

    handler.assert_awaited_once()
