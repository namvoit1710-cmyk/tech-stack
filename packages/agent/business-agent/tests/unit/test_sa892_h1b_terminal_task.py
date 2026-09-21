"""SA-892 (task-scoped stop) H1b: a terminal task is never flipped to PROCESSING.

``_get_current_task`` used to call ``update_task(..., status=PROCESSING)``
UNCONDITIONALLY, so a continuation that passed the turn-entry guard but whose
task was CANCELLED by a racing Stop would flip CANCELLED -> PROCESSING and run,
erasing the stop signal.

The fix reads the fresh task status first; if it is terminal (CANCELLED / SUCCESS
/ ERROR / FAIL) it does NOT overwrite it and raises ``TaskNotRunnableError``,
which ``execute()`` treats as a DROP (no flip, no plan, no dispatch).
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
    TaskNotRunnableError,
)


def _metadata(conv_id: str = "conv-1", current_task_id: str = "task-1") -> Metadata:
    return Metadata(
        conv_id=conv_id,
        checkpoint_id="CP-01",
        current_task_id=current_task_id,
        current_message_id="msg-1",
    )


def _task(status: TaskStatus, task_id: str = "task-1") -> Task:
    return Task(
        task_id=task_id,
        agent="agent.file",
        name="do a thing",
        status=status,
    )


def _cpc_event(conv_id: str = "conv-1") -> EventDTO:
    # CONVERSATION_PLAN_CREATED continuation: no payload step id.
    return EventDTO(
        id="event-1",
        type=QueueRequestType.CONVERSATION_PLAN_CREATED,
        conv_id=conv_id,
        correlation_id="corr-1",
        payload=None,
        metadata={"source": "test"},
    )


def _usecase(get_task):
    update_task = Mock()
    publisher = SimpleNamespace(publish=AsyncMock())
    repository = SimpleNamespace(
        get_metadata=Mock(return_value=_metadata()),
        get_task=get_task,
        update_task=update_task,
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
    return subject, repository, publisher, update_task


# ── _get_current_task unit behavior ──


@pytest.mark.asyncio
async def test_get_current_task_does_not_flip_cancelled_task():
    """A CANCELLED task raises TaskNotRunnableError and is NOT flipped."""
    subject, _repo, _pub, update_task = _usecase(
        Mock(return_value=_task(TaskStatus.CANCELLED))
    )
    with pytest.raises(TaskNotRunnableError):
        await subject._get_current_task("conv-1", _metadata())
    update_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal",
    [TaskStatus.CANCELLED, TaskStatus.SUCCESS, TaskStatus.ERROR, TaskStatus.FAIL],
)
async def test_get_current_task_all_terminal_states_are_not_flipped(terminal):
    subject, _repo, _pub, update_task = _usecase(Mock(return_value=_task(terminal)))
    with pytest.raises(TaskNotRunnableError):
        await subject._get_current_task("conv-1", _metadata())
    update_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [TaskStatus.PENDING, TaskStatus.PROCESSING])
async def test_get_current_task_flips_live_task_to_processing(live):
    """Happy path unchanged: a PENDING/PROCESSING task is flipped to PROCESSING."""
    subject, _repo, _pub, update_task = _usecase(Mock(return_value=_task(live)))
    result = await subject._get_current_task("conv-1", _metadata())
    update_task.assert_called_once_with(
        "conv-1", "task-1", status=TaskStatus.PROCESSING
    )
    assert result.status == live


# ── execute() end-to-end: the race repro ──


@pytest.mark.asyncio
async def test_race_cancel_after_entry_guard_drops_turn_without_dispatch():
    """The H1b race: the turn-entry guard reads the task as still live (PROCESSING)
    and lets the turn in; a Stop then cancels the task; ``_get_current_task``'s
    fresh read sees CANCELLED and DROPS the turn — no flip, no publish.

    Pre-fix (``_get_current_task`` flipped unconditionally) this same setup flipped
    the task to PROCESSING and proceeded into planning."""
    # 1st get_task = the entry guard (sees PROCESSING -> passes);
    # 2nd get_task = _get_current_task (sees CANCELLED -> drop).
    get_task = Mock(
        side_effect=[_task(TaskStatus.PROCESSING), _task(TaskStatus.CANCELLED)]
    )
    subject, _repo, publisher, update_task = _usecase(get_task)
    # keep the real _execute_conversation_plan_created so the drop happens inside it
    await subject.execute(_cpc_event())

    update_task.assert_not_called()  # CANCELLED never flipped to PROCESSING
    publisher.publish.assert_not_awaited()  # no downstream dispatch
    assert get_task.call_count == 2
