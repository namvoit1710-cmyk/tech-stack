"""A CREATED run must be cancellable — it was the only state with no exit.

``CancelRunUseCase`` accepted RUNNING, PAUSED, PENDING and SUSPENDED. CREATED
was refused, which meant a run that was written but never started had no path
to a terminal state at all. Measured on tenant-1: 117 such runs, the oldest
stuck for 31 days.

That gap has a sharper consequence than a stale row. Every per-run resource is
released on a TERMINAL transition and only there — resolver caches, the three
projector view-store dicts, the per-run asyncio.Lock. And because run status is
event-sourced, the obvious workaround (UPDATE the row) would move the table
while the projector kept folding the old status from the event log, leaving the
API and the database permanently disagreeing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import (
    CancelRunCommand,
    CancelRunUseCase,
)


def _run(status: str) -> WorkflowRun:
    run = WorkflowRun(id="run-1", workflow_id="wf-1", workflow_version="1")
    run.status = status
    return run


def _use_case(run: WorkflowRun):
    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=run)
    run_repo.asave = AsyncMock()
    run_repo.aget_by_id_version = AsyncMock(return_value=run)

    task_repo = MagicMock()
    task_repo.alist_by_run = AsyncMock(return_value=[])

    engine = MagicMock()
    engine.emit_run_status = MagicMock()
    engine.drain_events = AsyncMock()

    uc = CancelRunUseCase(
        logger=MagicMock(),
        run_repo=run_repo,
        task_repo=task_repo,
        stage_recorder=MagicMock(),
        orchestration_engine=engine,
        # None so ``read_authoritative_status`` falls back to the aggregate;
        # the projector's own view is exercised by its own tests.
        run_context_view_store=None,
    )
    return uc, engine, run_repo


class TestCreatedIsCancellable:
    @pytest.mark.asyncio
    async def test_a_created_run_can_be_cancelled(self):
        run = _run(RunStatus.CREATED.value)
        uc, engine, _ = _use_case(run)

        result = await uc.execute(CancelRunCommand(run_id="run-1"))

        assert not result.error_code, (
            f"CREATED was refused: {result.error_code} {result.error}"
        )

    @pytest.mark.asyncio
    async def test_it_goes_through_the_event_path_not_a_field_write(self):
        """The projector rebuilds status from RunCancelled. If the use case
        stopped emitting and only set a field, the API would keep reporting
        the old status — which is exactly the split a direct SQL UPDATE would
        have created."""
        run = _run(RunStatus.CREATED.value)
        uc, engine, _ = _use_case(run)

        await uc.execute(CancelRunCommand(run_id="run-1"))

        engine.emit_run_status.assert_called_once()
        args, kwargs = engine.emit_run_status.call_args
        assert "cancelled" in args or kwargs.get("status") == "cancelled"


class TestTheOtherStatesAreUnchanged:
    @pytest.mark.parametrize("status", [
        RunStatus.RUNNING.value,
        RunStatus.PAUSED.value,
        RunStatus.PENDING.value,
        RunStatus.SUSPENDED.value,
    ])
    @pytest.mark.asyncio
    async def test_still_accepted(self, status):
        uc, _, _ = _use_case(_run(status))
        result = await uc.execute(CancelRunCommand(run_id="run-1"))
        assert not result.error_code, f"{status} regressed: {result.error_code}"

    @pytest.mark.parametrize("status", [
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ])
    @pytest.mark.asyncio
    async def test_terminal_states_are_still_refused(self, status):
        """Widening the gate must not make it possible to re-cancel a finished
        run — that would emit a second terminal event into the log."""
        uc, engine, _ = _use_case(_run(status))
        result = await uc.execute(CancelRunCommand(run_id="run-1"))
        assert result.error_code, f"{status} was wrongly accepted"
        engine.emit_run_status.assert_not_called()


class TestTheStartGuardThatMakesThisSafe:
    """Widening the cancel gate is only safe because the START is guarded.

    ``_orchestrate_start_inner`` had no status check. A StartRunAction landing
    after a cancel would dispatch entry nodes onto a cancelled run and bring it
    back. That was the real reason CREATED was refused — and refusing the
    cancel hid the race rather than fixing it, because the same StartRunAction
    could equally arrive after a PENDING run was cancelled.
    """

    @pytest.mark.asyncio
    async def test_a_cancelled_run_is_not_started(self):
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )

        run = _run(RunStatus.CREATED.value)   # the AGGREGATE still says CREATED

        # ...while the projector — the authoritative reader — says CANCELLED.
        # This split is not contrived: a cancel deliberately does not write the
        # aggregate's STATUS column (Phase 3b.2c).
        view = MagicMock()
        view.status = RunStatus.CANCELLED.value
        view_store = MagicMock()
        view_store.get = MagicMock(return_value=view)

        run_repo = MagicMock()
        run_repo.aget_by_id = AsyncMock(return_value=run)
        run_repo.asave = AsyncMock()

        engine = OrchestrationEngine(
            run_repo=run_repo,
            task_repo=MagicMock(),
            workflow_repo=MagicMock(),
            event_publisher=MagicMock(),
            handler_registry=MagicMock(),
            task_factory=MagicMock(),
            successor_processor=MagicMock(),
            stage_recorder=MagicMock(),
            run_context_view_store=view_store,
        )
        engine.load_workflow_for_run = MagicMock(
            side_effect=AssertionError(
                "the engine loaded the workflow for a CANCELLED run — it got "
                "past the guard and would have dispatched entry nodes"
            )
        )

        await engine._orchestrate_start_inner("run-1", run)
        run_repo.asave.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_guard_on_run_status_would_not_have_worked(self):
        """Documents WHY the guard reads the view store.

        The aggregate keeps saying CREATED after a successful cancel, so a
        guard written the obvious way — ``if run.status in TERMINAL`` — would
        look right in review and never fire in production.
        """
        from app.layer2_application.run_status_reader import (
            read_authoritative_status,
        )

        run = _run(RunStatus.CREATED.value)
        view = MagicMock()
        view.status = RunStatus.CANCELLED.value
        store = MagicMock()
        store.get = MagicMock(return_value=view)

        assert run.status == RunStatus.CREATED.value      # the trap
        assert read_authoritative_status(run, store) == RunStatus.CANCELLED.value
