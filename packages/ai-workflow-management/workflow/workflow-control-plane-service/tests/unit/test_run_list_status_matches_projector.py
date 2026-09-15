"""``GET /api/v1/runs`` reads the STATUS column, and cancel stopped writing it.

Run status is event-sourced: ``RunStatusChanged`` goes into the log, the
projector folds it, and ``GET /api/v1/runs/{id}`` serves that. But the *list*
endpoint is not event-sourced and cannot be — ``ListRunsUseCase`` calls
``run_repo.list_runs``, which is SQL over the runs table: it selects STATUS,
filters ``STATUS IN (...)`` and orders by STARTED_AT. A per-run event replay
serves none of those three.

Phase 3b.2c removed the aggregate status write from ``cancel_run`` and from the
child-run timeout sweep, and only those two — completed, failed, paused,
resumed, suspended and pending all kept writing it. So those two transitions
became invisible to the only query the run list can run.

Measured on tenant-1 2026-08-13, before the fix:

    GET /runs?status=RUNNING      → 8 rows, 3 of them CANCELLED per the projector
    GET /runs?status=CANCELLED    → 0 rows, tenant-wide
    GET /runs/427ae40b…           → "RUNNING"    (list, from the column)
    GET /runs/427ae40b…/          → "CANCELLED"  (detail, from the projector)

The two read paths disagreed on the same run, and the disagreement was
permanent: nothing else ever writes that column again.

These tests pin the list read path specifically. The projector keeps its
authority — ``TestTheProjectorIsStillTheAuthority`` holds that line, because the
fix is "keep the index current", not "move the source of truth back onto the
aggregate".
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import (
    CancelRunCommand,
    CancelRunUseCase,
)
from app.layer2_application.features.sweep_timed_out_child_runs.use_cases.sweep_timed_out_child_runs_use_case import (  # noqa: E501
    SweepTimedOutChildRunsUseCase,
)
from app.layer2_application.repositories.run_repository_interface import ListRunsFilter
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)


class _RowBackedRunRepo(MemoryRunRepository):
    """``MemoryRunRepository`` hands back the very object it stores.

    That would make this whole file lie. The use case assigns
    ``run.status`` on the instance it is holding, and with a shared
    reference that assignment alone would show up in ``list_runs`` —
    even if the CAS apply closure, the only write that reaches the
    table, never touched status.

    HANA re-reads a row. Copy in and copy out so these tests measure
    what was persisted, not what is in the caller's frame.
    """

    def get_by_id(self, id: str) -> WorkflowRun | None:
        run = super().get_by_id(id)
        return copy.deepcopy(run) if run is not None else None

    async def aget_by_id(self, id: str) -> WorkflowRun | None:
        return self.get_by_id(id)

    def save(self, run: WorkflowRun) -> None:
        super().save(copy.deepcopy(run))

    async def asave(self, run: WorkflowRun) -> None:
        self.save(run)

    # --- what the list endpoint actually sees -------------------------
    def status_in_the_list(self, run_id: str) -> str:
        for item in self.list_runs(ListRunsFilter(limit=100)).items:
            if item.id == run_id:
                return item.status.value if hasattr(item.status, "value") else str(item.status)
        raise AssertionError(f"{run_id} is not in the run list at all")

    def list_by_status(self, status: str) -> list[str]:
        page = self.list_runs(ListRunsFilter(statuses=[status], limit=100))
        return [r.id for r in page.items]


def _seed(repo: _RowBackedRunRepo, run_id: str, *, child_runs=None, **kw) -> WorkflowRun:
    run = WorkflowRun(
        id=run_id,
        workflow_id=kw.pop("workflow_id", "wf-1"),
        workflow_version="1",
        started_at=kw.pop("started_at", "2026-08-13T03:00:46+00:00"),
        **kw,
    )
    run.status = kw.get("status", RunStatus.RUNNING)
    if child_runs:
        # Pre-step-5b legacy map — the only child source when no view store
        # is wired, which is how these tests reach the cascade.
        run.context.child_runs = dict(child_runs)
    repo.save(run)
    return run


def _cancel_use_case(repo, *, task_repo=None, view_store=None):
    engine = MagicMock()
    engine.emit_run_status = MagicMock()
    engine.emit_node_cancelled = MagicMock()
    engine.emit_child_run_cancelled = MagicMock()
    uc = CancelRunUseCase(
        logger=MagicMock(),
        run_repo=repo,
        task_repo=task_repo or _empty_task_repo(),
        stage_recorder=MagicMock(),
        orchestration_engine=engine,
        run_context_view_store=view_store,
    )
    return uc, engine


def _empty_task_repo():
    task_repo = MagicMock()
    task_repo.alist_by_run = AsyncMock(return_value=[])
    task_repo.asave = AsyncMock()
    return task_repo


class TestCancelIsVisibleToTheList:
    @pytest.mark.asyncio
    async def test_a_cancelled_run_does_not_read_running_in_the_list(self):
        repo = _RowBackedRunRepo()
        _seed(repo, "run-1")
        uc, _ = _cancel_use_case(repo)

        result = await uc.execute(CancelRunCommand(run_id="run-1"))

        assert result.cancelled, result.error
        assert repo.status_in_the_list("run-1") == RunStatus.CANCELLED.value, (
            "the run list still reports the pre-cancel status — this is the "
            "tenant-1 symptom: completed_at set, status RUNNING, forever"
        )

    @pytest.mark.asyncio
    async def test_the_status_filter_finds_it(self):
        """``?status=CANCELLED`` matched 0 rows tenant-wide. The filter is SQL
        on the same column, so a stale column doesn't just mislabel the row —
        it removes it from one query and adds it to the wrong one."""
        repo = _RowBackedRunRepo()
        _seed(repo, "run-1")
        uc, _ = _cancel_use_case(repo)

        await uc.execute(CancelRunCommand(run_id="run-1"))

        assert repo.list_by_status(RunStatus.CANCELLED.value) == ["run-1"]
        assert repo.list_by_status(RunStatus.RUNNING.value) == [], (
            "a cancelled run is still being served under ?status=RUNNING"
        )

    @pytest.mark.asyncio
    async def test_completed_at_alone_was_never_enough(self):
        """The row already carried a ``completed_at`` before this fix. Any
        consumer inferring "finished" from that had to guess *how* it
        finished — and the column said RUNNING."""
        repo = _RowBackedRunRepo()
        _seed(repo, "run-1")
        uc, _ = _cancel_use_case(repo)

        await uc.execute(CancelRunCommand(run_id="run-1"))

        row = repo.get_by_id("run-1")
        assert row.completed_at, "completed_at regressed"
        assert row.status == RunStatus.CANCELLED, (
            f"completed_at is set but status is {row.status} — the exact "
            "inconsistent row shape this fix exists to prevent"
        )


class TestTheChildCascadeToo:
    @pytest.mark.asyncio
    async def test_a_cascaded_child_is_cancelled_in_the_list(self):
        repo = _RowBackedRunRepo()
        _seed(repo, "parent-1", child_runs={"node-1": "child-1"})
        _seed(repo, "child-1", parent_run_id="parent-1", depth_level=1)

        uc, engine = _cancel_use_case(repo)
        await uc.execute(CancelRunCommand(run_id="parent-1"))

        assert repo.status_in_the_list("child-1") == RunStatus.CANCELLED.value, (
            "the parent cancelled but the child still reads RUNNING in the "
            "list — the cascade is only half-visible"
        )
        assert sorted(repo.list_by_status(RunStatus.CANCELLED.value)) == [
            "child-1", "parent-1",
        ]


class TestSweepTimeoutIsVisibleToTheList:
    """The child-run timeout sweep is the second transition Phase 3b.2c left
    without a column write, and it carries an extra cost: the sweep discovers
    work with ``STATUS IN ('RUNNING')``, so a child it already failed came back
    on every later pass and was only stopped by the authoritative-status guard.
    """

    def _sweep_setup(self):
        repo = _RowBackedRunRepo()
        _seed(repo, "parent-1", child_runs={"node-1": "child-1"})
        _seed(repo, "child-1", parent_run_id="parent-1", depth_level=1)

        tracking_task = MagicMock()
        tracking_task.id = "task-1"
        tracking_task.run_id = "parent-1"
        tracking_task.node_id = "node-1"
        tracking_task.status = TaskStatus.RUNNING

        engine = MagicMock()
        engine.emit_run_status = MagicMock()
        engine.emit_node_cancelled = MagicMock()
        # None short-circuits the parent fail-propagation, which is not what
        # this test is about.
        engine.load_workflow_for_run = MagicMock(return_value=None)

        uc = SweepTimedOutChildRunsUseCase(
            logger=MagicMock(),
            task_repo=_empty_task_repo(),
            run_repo=repo,
            orchestration_engine=engine,
            run_context_view_store=None,
        )
        return uc, repo, tracking_task, engine

    @pytest.mark.asyncio
    async def test_a_timed_out_child_reads_failed_in_the_list(self):
        uc, repo, tracking_task, _ = self._sweep_setup()

        await uc._timeout_child_run(tracking_task, timeout_ms=1000)

        assert repo.status_in_the_list("child-1") == RunStatus.FAILED.value
        row = repo.get_by_id("child-1")
        assert row.error and "timed out" in row.error

    @pytest.mark.asyncio
    async def test_the_sweep_stops_rediscovering_it(self):
        uc, repo, tracking_task, _ = self._sweep_setup()

        await uc._timeout_child_run(tracking_task, timeout_ms=1000)

        still_active = await uc._list_active_child_runs()
        assert [r.id for r in still_active] == [], (
            "the sweep re-listed a child it had already failed — it will do "
            "that on every pass for the life of the row"
        )


class TestTheProjectorIsStillTheAuthority:
    """Writing the column must not turn it back into the source of truth."""

    @pytest.mark.asyncio
    async def test_the_event_is_still_emitted(self):
        repo = _RowBackedRunRepo()
        _seed(repo, "run-1")
        uc, engine = _cancel_use_case(repo)

        await uc.execute(CancelRunCommand(run_id="run-1"))

        engine.emit_run_status.assert_called_once()
        args, kwargs = engine.emit_run_status.call_args
        assert "cancelled" in args or kwargs.get("status") == "cancelled"

    def test_the_view_store_still_wins_when_the_two_disagree(self):
        from app.layer2_application.run_status_reader import (
            read_authoritative_status,
        )

        run = WorkflowRun(id="run-1", workflow_id="wf-1", workflow_version="1")
        run.status = RunStatus.CANCELLED          # the index
        view = MagicMock()
        view.status = RunStatus.RUNNING.value     # the log
        store = MagicMock()
        store.get = MagicMock(return_value=view)

        assert read_authoritative_status(run, store) == RunStatus.RUNNING.value, (
            "a guard now trusts the aggregate over the event log — that is "
            "the regression this fix must not cause"
        )

    @pytest.mark.asyncio
    async def test_the_gate_is_still_read_from_the_projector(self):
        """A run the column calls RUNNING but the log calls CANCELLED must
        still be refused. Keeping the index current does not license reading
        the gate off it."""
        repo = _RowBackedRunRepo()
        _seed(repo, "run-1")
        view = MagicMock()
        view.status = RunStatus.CANCELLED.value
        store = MagicMock()
        store.get = MagicMock(return_value=view)
        store.child_runs = {}

        uc, engine = _cancel_use_case(repo, view_store=store)
        result = await uc.execute(CancelRunCommand(run_id="run-1"))

        assert result.error_code, "an already-cancelled run was cancelled twice"
        engine.emit_run_status.assert_not_called()
