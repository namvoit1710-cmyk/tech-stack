"""Idempotent-submit-after-cancel regression tests.

The FE's reject button issues two requests on INPUT / FILE_UPLOAD /
DATA_EDIT pending nodes:

    1. PATCH /data {data: {_action: "reject"}}   — cancels the task
    2. POST  /submit                              — finalize the action

Step 1 routes through ``EditNodeDataUseCase._cancel_pending_task``: it
sets ``task.status = FAILED``, ``task.error = "user_cancelled"``,
emits ``NodeCancelled``, and drives ``orchestrate_fail_locked`` so the
failure edge runs (or the run terminates as FAILED when no edge exists).

Step 2 must NOT 409 just because step 1 already finalized the task. The
submit is an idempotent no-op when the task is already user-cancelled.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.submit_node.use_cases.submit_node_use_case import (
    SubmitNodeCommand,
    SubmitNodeResult,
    SubmitNodeUseCase,
)


# ─────────────────────────── helpers ─────────────────────────────


def _make_task(
    *,
    task_id: str = "t1",
    run_id: str = "r1",
    node_id: str = "n1",
    status: TaskStatus = TaskStatus.PENDING,
    error: str = "",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.run_id = run_id
    task.node_id = node_id
    task.status = status
    task.error = error
    task.created_at = created_at
    return task


def _make_usecase(
    *,
    task_by_id: dict[str, MagicMock] | None = None,
    tasks_by_run: dict[str, list[MagicMock]] | None = None,
    delegate: AsyncMock | None = None,
) -> tuple[SubmitNodeUseCase, AsyncMock]:
    """Wire a minimal SubmitNodeUseCase. ``delegate`` is the
    ResumePendingTaskUseCase stand-in; defaults to a success result."""
    task_repo = MagicMock()
    task_repo.get_by_id = MagicMock(
        side_effect=lambda tid: (task_by_id or {}).get(tid),
    )
    task_repo.list_by_run = MagicMock(
        side_effect=lambda rid: (tasks_by_run or {}).get(rid, []),
    )

    run_repo = MagicMock()
    run_repo.get_by_id = MagicMock(
        return_value=MagicMock(child_run_ids=[]),
    )

    if delegate is None:
        delegate = AsyncMock()
        delegate.execute = AsyncMock(return_value=MagicMock(
            task_id="t1", status="COMPLETED", resumed=True,
            error=None, error_code="", error_details={},
        ))

    uc = SubmitNodeUseCase(
        logger=MagicMock(),
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=MagicMock(),
        stage_recorder=MagicMock(),
        resume_pending_task_usecase=delegate,
    )
    return uc, delegate


# ─────────────────── idempotent-cancel behaviour ─────────────────


@pytest.mark.asyncio
class TestSubmitAfterCancelExplicitTaskId:
    async def test_user_cancelled_task_returns_200_without_delegating(self):
        # PATCH /data {_action: reject} already finalized this task as
        # FAILED + user_cancelled. The follow-up POST /submit must not
        # 409 — it returns 200 with the cancelled state and skips the
        # ResumePendingTaskUseCase delegate (which would 409 because
        # the task is not PENDING / the run may already be FAILED).
        cancelled = _make_task(
            status=TaskStatus.FAILED, error="user_cancelled",
        )
        uc, delegate = _make_usecase(task_by_id={"t1": cancelled})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id="t1",
        ))

        assert isinstance(result, SubmitNodeResult)
        assert result.error is None
        assert result.error_code == ""
        assert result.task_id == "t1"
        assert result.node_state == "FAILED"
        assert result.downstream_dispatched is False
        delegate.execute.assert_not_awaited()

    async def test_failed_task_with_other_error_still_409s(self):
        # A real failure (not user_cancelled) keeps its 409 contract —
        # the FE shouldn't be able to "submit" a task that died in the
        # worker.
        failed = _make_task(
            status=TaskStatus.FAILED, error="worker_exception",
        )
        uc, delegate = _make_usecase(task_by_id={"t1": failed})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id="t1",
        ))

        assert result.error_code == ErrorCode.INVALID_TASK_STATUS.code
        delegate.execute.assert_not_awaited()

    async def test_pending_task_still_routes_through_delegate(self):
        # Sanity: the idempotent short-circuit must not bypass the
        # normal happy path for a PENDING task.
        pending = _make_task(status=TaskStatus.PENDING)
        uc, delegate = _make_usecase(task_by_id={"t1": pending})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id="t1",
        ))

        assert result.error is None
        assert result.node_state == "COMPLETED"
        delegate.execute.assert_awaited_once()

    async def test_task_id_does_not_belong_to_node_returns_404(self):
        # Pre-fix behaviour preserved: explicit task_id pointing at a
        # different node still returns TASK_NOT_FOUND, even if that
        # other task is user-cancelled.
        other_node_cancelled = _make_task(
            node_id="other-node",
            status=TaskStatus.FAILED, error="user_cancelled",
        )
        uc, delegate = _make_usecase(
            task_by_id={"t1": other_node_cancelled},
        )

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id="t1",
        ))

        assert result.error_code == ErrorCode.TASK_NOT_FOUND.code
        delegate.execute.assert_not_awaited()


@pytest.mark.asyncio
class TestSubmitAfterCancelByNodeScan:
    async def test_scan_returns_idempotent_when_latest_is_user_cancelled(self):
        # FE called POST /submit without ``?task_id=`` after a cancel.
        # The scan finds no PENDING tasks; the most recent task on the
        # node is user-cancelled → return 200 idempotently.
        cancelled = _make_task(
            status=TaskStatus.FAILED, error="user_cancelled",
            created_at="2026-01-01T01:00:00+00:00",
        )
        uc, delegate = _make_usecase(tasks_by_run={"r1": [cancelled]})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id=None,
        ))

        assert result.error is None
        assert result.node_state == "FAILED"
        delegate.execute.assert_not_awaited()

    async def test_scan_prefers_pending_over_user_cancelled_predecessor(self):
        # A loop iteration may have a prior user-cancelled task plus a
        # newer PENDING one for the same node. The scan must still
        # surface the PENDING task and route it through the delegate.
        old_cancelled = _make_task(
            task_id="t-old",
            status=TaskStatus.FAILED, error="user_cancelled",
            created_at="2026-01-01T00:00:00+00:00",
        )
        new_pending = _make_task(
            task_id="t-new",
            status=TaskStatus.PENDING,
            created_at="2026-01-01T01:00:00+00:00",
        )
        uc, delegate = _make_usecase(
            tasks_by_run={"r1": [old_cancelled, new_pending]},
        )

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id=None,
        ))

        assert result.error is None
        delegate.execute.assert_awaited_once()
        call_kwargs = delegate.execute.await_args.args[0]
        # Delegate must be called against the PENDING task, not the
        # cancelled predecessor.
        assert call_kwargs.task_id == "t-new"

    async def test_scan_with_no_tasks_on_node_returns_404(self):
        # No tasks at all → preserve the original TASK_NOT_FOUND behaviour.
        uc, delegate = _make_usecase(tasks_by_run={"r1": []})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id=None,
        ))

        assert result.error_code == ErrorCode.TASK_NOT_FOUND.code
        delegate.execute.assert_not_awaited()

    async def test_scan_with_only_non_cancel_failed_task_returns_409(self):
        # A node whose only task is FAILED for a real reason (not
        # user_cancelled) must still 409 — only the cancel short-circuit
        # is treated as idempotent-success.
        failed = _make_task(
            status=TaskStatus.FAILED, error="worker_exception",
        )
        uc, delegate = _make_usecase(tasks_by_run={"r1": [failed]})

        result = await uc.execute(SubmitNodeCommand(
            run_id="r1", node_id="n1", task_id=None,
        ))

        assert result.error_code == ErrorCode.INVALID_TASK_STATUS.code
        delegate.execute.assert_not_awaited()
