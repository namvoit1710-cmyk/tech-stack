"""SA-892 (AC4): business-agent ReviewPlanUseCase per-task stop guard.

The step-result path. A delegated step / technical-agent RESULT comes back as an
``executor.step.status`` event handled by ``ReviewPlanUseCase``. If a Stop already
cancelled that step's own task (Task.status == CANCELLED), the LATE result must be
DROPPED — no review, no ``update_task`` (which would flip the task
PROCESSING/ERROR), and no push (which would re-enable the chat or show a Retry
button on the stopped conversation). Without the guard the late result is
processed and leaks past Stop (a partial resurrection) — the failing repro found
live by the "call echo agent → Stop before it responds" test.

Contrast: a result whose task is still PROCESSING (a live turn a Stop never
cancelled) is handled as normal.

Mirrors ``test_sa892_agent_stop_guard`` (the CreatePlanUseCase continuation path).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import Task, TaskStatus
from app.layer1_domain.entities.queue_event import (
    QueueEvent,
    QueuePayload,
    QueueRequestType,
    QueueStep,
)
from app.layer2_application.features.main_agent.use_cases.review_plan_usecase import (
    ReviewPlanUseCase,
)


def _task(status: TaskStatus, task_id: str = "step-1") -> Task:
    return Task(
        task_id=task_id,
        agent="agent.echo",
        name="echo step",
        status=status,
    )


def _event(task_id: str = "step-1", status: str = "error") -> QueueEvent:
    # An executor.step.status result carries the step id (== Task.id) on its payload.
    step = QueueStep(
        id=task_id,
        execute_by="agent.echo",
        goal="Echo",
        message="Echo",
        status=status,
    )
    payload = QueuePayload(
        from_="executor.runtime", to="agent.echo-caller", steps=[step], type=None
    )
    return QueueEvent(
        message_id="msg-1",
        type=QueueRequestType.EXECUTOR_STEP_STATUS.value,
        conv_id="conv-1",
        correlation_id="corr-1",
        payload=payload,
    )


def _usecase(task_status: TaskStatus):
    repository = SimpleNamespace(
        get_task=Mock(return_value=_task(task_status)),
        get_metadata=Mock(return_value=SimpleNamespace(main_conv_id=None)),
        update_task=Mock(),
    )
    push = AsyncMock()
    logger = SimpleNamespace(info=Mock(), warning=Mock(), error=Mock(), debug=Mock())
    subject = ReviewPlanUseCase(
        reviewer_resolver=SimpleNamespace(resolve=Mock(return_value=Mock())),
        push_gateway_notifier=push,
        agent_shared_state_repository=repository,
        publisher=AsyncMock(),
        uuid_builder=SimpleNamespace(create_uuid7=Mock(return_value="uuid-1")),
        settings=SimpleNamespace(),
        logger=logger,
    )
    return subject, repository, push


@pytest.mark.asyncio
async def test_cancelled_task_step_result_is_dropped():
    """The key repro: a late step result whose task is CANCELLED is dropped — the
    task is not updated and nothing is pushed to the (stopped) conversation."""
    subject, repository, push = _usecase(TaskStatus.CANCELLED)

    await subject._execute_executor_step_status(_event())

    assert push.method_calls == []  # no chat:enable / Retry / task-status push
    assert push.await_count == 0
    repository.update_task.assert_not_called()  # no flip to PROCESSING/ERROR
    repository.get_task.assert_called_with("conv-1", "step-1")


@pytest.mark.asyncio
async def test_guard_true_for_cancelled_task():
    subject, _repository, _push = _usecase(TaskStatus.CANCELLED)
    assert subject._incoming_step_task_is_cancelled(_event()) is True


@pytest.mark.asyncio
async def test_guard_false_for_processing_task():
    subject, _repository, _push = _usecase(TaskStatus.PROCESSING)
    assert subject._incoming_step_task_is_cancelled(_event()) is False


@pytest.mark.asyncio
async def test_guard_falls_through_when_task_unresolved():
    """Best-effort: an unresolved task (get_task -> None) must NOT drop a legit
    result — the guard returns False so the review continues."""
    subject, repository, _push = _usecase(TaskStatus.CANCELLED)
    repository.get_task = Mock(return_value=None)
    assert subject._incoming_step_task_is_cancelled(_event()) is False


@pytest.mark.asyncio
async def test_guard_falls_through_on_lookup_error():
    """A failing repository read must not block a result — treated as not cancelled."""
    subject, repository, _push = _usecase(TaskStatus.CANCELLED)
    repository.get_task = Mock(side_effect=RuntimeError("db down"))
    assert subject._incoming_step_task_is_cancelled(_event()) is False
