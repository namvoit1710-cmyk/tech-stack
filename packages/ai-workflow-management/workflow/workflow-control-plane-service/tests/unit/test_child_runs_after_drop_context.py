"""Sub-workflow discovery must survive ``DROP CONTEXT``.

``89f55519a`` (step 5b) removed ``CONTEXT`` from ``hana_run_repo._DATA_COLUMNS``.
Nine call sites still read ``run.context.child_runs`` off the aggregate, so on
every run created since 2026-08-08 they saw ``{}``: the frontend's run-history
drill-in went blank, and — quieter and worse — ``cancel_run`` stopped cascading
and left child runs RUNNING.

Why the existing suite did not catch it: every child-run test populates a
``MemoryRunRepository`` and reads it straight back, so ``context.child_runs``
survives the round trip that HANA no longer performs. The tests modelled the
aggregate the code *used* to have. These model the one it has now — **the
aggregate's context is empty and the projector is the only source** — which is
the only shape that can fail.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import (
    CancelRunCommand,
    CancelRunUseCase,
)
from app.layer2_application.features.list_run_children.use_cases.list_run_children_use_case import (
    ListRunChildrenCommand,
    ListRunChildrenUseCase,
)
from app.layer2_application.read.run_state import child_runs_of
from tests.conftest import DummyLogger


class ProjectorViewStoreDouble:
    """A view store whose ``child_runs`` come from an explicit map.

    Deliberately NOT ``LiveViewStoreDouble``: that one reads the run repo, so
    it would go empty in exactly the scenario under test. Production's store is
    event-sourced from ``ChildRunStarted`` and is populated when the aggregate
    is not — that independence is the whole point.
    """

    def __init__(
        self,
        by_run: dict[str, dict[str, str]],
        statuses: dict[str, str] | None = None,
    ) -> None:
        self._by_run = by_run
        # ``cancel_run`` reads authoritative status through this same store.
        self._statuses = statuses or {}

    def get(self, run_id: str):
        if run_id not in self._by_run:
            return None
        return SimpleNamespace(
            child_runs=dict(self._by_run[run_id]),
            status=self._statuses.get(run_id, RunStatus.RUNNING.value),
        )


class ExplodingViewStore:
    def get(self, run_id: str):
        raise RuntimeError("event log unavailable")


# ---------------------------------------------------------------------------
# the chokepoint itself
# ---------------------------------------------------------------------------

class TestChildRunsOf:
    def test_reads_the_projector_when_the_aggregate_is_empty(self):
        """The post-step-5b shape: coordinator assigned, HANA dropped it."""
        run = WorkflowRun(id="p", workflow_id="wf", context=RunContext())
        assert run.context.child_runs == {}

        store = ProjectorViewStoreDouble({"p": {"node-a": "child-a"}})
        assert child_runs_of("p", store, run) == {"node-a": "child-a"}

    def test_still_reads_a_pre_step_5b_aggregate(self):
        """Runs written before the migration kept a populated CONTEXT column;
        their projector may predate nothing at all, so the legacy map is still
        merged rather than replaced."""
        run = WorkflowRun(
            id="p", workflow_id="wf",
            context=RunContext(child_runs={"node-old": "child-old"}),
        )
        store = ProjectorViewStoreDouble({})
        assert child_runs_of("p", store, run) == {"node-old": "child-old"}

    def test_merges_both_and_the_projector_wins(self):
        run = WorkflowRun(
            id="p", workflow_id="wf",
            context=RunContext(child_runs={
                "node-old": "child-old",
                "node-a": "STALE",
            }),
        )
        store = ProjectorViewStoreDouble({"p": {"node-a": "child-a"}})
        assert child_runs_of("p", store, run) == {
            "node-old": "child-old",
            "node-a": "child-a",          # projector is the live writer
        }

    def test_no_view_store_degrades_to_the_aggregate(self):
        run = WorkflowRun(
            id="p", workflow_id="wf",
            context=RunContext(child_runs={"n": "c"}),
        )
        assert child_runs_of("p", None, run) == {"n": "c"}

    def test_a_failing_view_store_raises_rather_than_reporting_no_children(self):
        """An empty answer is indistinguishable from 'no children', and every
        caller treats that as 'nothing to cancel / nothing to show'. That is the
        failure mode this module exists to end, so it must not be reachable by
        swallowing an error."""
        with pytest.raises(RuntimeError):
            child_runs_of("p", ExplodingViewStore(), None)


# ---------------------------------------------------------------------------
# the frontend symptom
# ---------------------------------------------------------------------------

class TestListRunChildrenFromProjector:
    async def test_lists_children_when_only_the_projector_knows_them(self, run_repo):
        run_repo.save(WorkflowRun(
            id="parent", workflow_id="wf-parent", status=RunStatus.RUNNING,
            context=RunContext(),                      # CONTEXT is gone
        ))
        run_repo.save(WorkflowRun(
            id="child-1", workflow_id="wf-c1", status=RunStatus.COMPLETED,
            started_at="2026-08-09T00:00:00Z", completed_at="2026-08-09T00:01:00Z",
        ))
        usecase = ListRunChildrenUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            run_context_view_store=ProjectorViewStoreDouble(
                {"parent": {"subflow-node-1": "child-1"}},
            ),
        )

        result = await usecase.execute(ListRunChildrenCommand(run_id="parent"))

        assert result.error is None
        assert len(result.children) == 1, "run history drill-in went blank"
        child = result.children[0]
        assert child["parent_node_id"] == "subflow-node-1"
        assert child["child_run_id"] == "child-1"
        assert child["workflow_id"] == "wf-c1"
        assert child["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# the dangerous one — orphaned children
# ---------------------------------------------------------------------------

class TestCancelCascadesFromProjector:
    async def test_cancelling_a_parent_still_cancels_its_child(self):
        parent = WorkflowRun(id="parent", workflow_id="wf-p", context=RunContext())
        parent.status = RunStatus.RUNNING
        child = WorkflowRun(id="child-1", workflow_id="wf-c")
        child.status = RunStatus.RUNNING

        by_id = {"parent": parent, "child-1": child}
        run_repo = MagicMock()
        run_repo.aget_by_id = AsyncMock(side_effect=lambda rid: by_id.get(rid))
        run_repo.aget_by_id_version = AsyncMock(side_effect=lambda rid, *_: by_id.get(rid))
        run_repo.asave = AsyncMock()

        task_repo = MagicMock()
        task_repo.alist_by_run = AsyncMock(return_value=[])

        engine = MagicMock()
        engine.emit_run_status = MagicMock()
        engine.emit_child_run_cancelled = MagicMock()
        engine.drain_events = AsyncMock()

        uc = CancelRunUseCase(
            logger=MagicMock(),
            run_repo=run_repo,
            task_repo=task_repo,
            stage_recorder=MagicMock(),
            orchestration_engine=engine,
            run_context_view_store=ProjectorViewStoreDouble(
                {"parent": {"subflow-node-1": "child-1"}},
            ),
        )

        await uc.execute(CancelRunCommand(run_id="parent"))

        cancelled = [
            call.args[0].id
            for call in engine.emit_run_status.call_args_list
            if len(call.args) >= 2 and call.args[1] == "cancelled"
        ]
        assert "child-1" in cancelled, (
            "parent cancelled but the child was left RUNNING — the cascade "
            "read an empty context map"
        )
