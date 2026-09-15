"""Spec item D — does MERGE still reach quorum after DROP CONTEXT?

The spec said to assess before fixing, and this file is the assessment.

MERGE counts arrivals in ``run.context.merge_quorum_buffers``, seeded each call
from ``run.context.completed_nodes`` / ``skipped_nodes``. Two facts about those
three fields, established by the guard in ``test_context_read_surface_guard``:

* ``merge_quorum_buffers`` is EPHEMERAL — absent from ``_context_to_dict``,
  ``RunContextSnapshot`` and ``run_projection`` alike. It has never survived a
  reload, and that was survivable precisely because the seed rebuilt it.
* ``completed_nodes`` / ``skipped_nodes`` rode ``CONTEXT``, which is now dropped.

So the seed that used to rebuild the lost buffer is itself empty now. What was a
benign ephemeral cache became the only record of a quorum, and it dies between
arrivals.

The distinguishing arrangement is TWO SEPARATE CALL FRAMES, each with its own
freshly reloaded run — which is what the orchestration worker actually does:
each predecessor completes via its own ContinueRunAction and its own
``aget_by_id``. A test that reuses one run object across both arrivals keeps the
buffer alive in memory and cannot fail.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects import NodeType, TaskStatus
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory

MERGE = "merge-node"
PRED_A, PRED_B = "pred-a", "pred-b"


def _workflow(wait_strategy: str = "all") -> Workflow:
    return Workflow(
        id="wf-1", name="fan-in",
        nodes=[
            WorkflowNode(id=PRED_A, name="A", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(id=PRED_B, name="B", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(id=MERGE, name="Merge", node_type=NodeType.MERGE,
                         config={"merge_strategy": wait_strategy}),
        ],
        edges=[
            WorkflowEdge(source_node_id=PRED_A, target_node_id=MERGE),
            WorkflowEdge(source_node_id=PRED_B, target_node_id=MERGE),
        ],
    )


def _reloaded_run() -> WorkflowRun:
    """A run exactly as the worker gets it back from HANA today: CONTEXT is
    gone, so every field it carried — including the quorum seed — is empty."""
    run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext())
    assert run.context.completed_nodes == set()
    assert run.context.skipped_nodes == set()
    assert run.context.merge_quorum_buffers == {}
    return run


def _arrival(node_id: str) -> Task:
    return Task(id=f"t-{node_id}", run_id="run-1", node_id=node_id,
                status=TaskStatus.COMPLETED)


class ProjectorDouble:
    """Accumulates completions the way the real projector does — each
    ``NodeCompleted`` folds in and stays. Deliberately independent of the run
    repo, because the aggregate is empty in the scenario under test."""

    def __init__(self, completed=None, skipped=None):
        self.completed = set(completed or ())
        self.skipped = set(skipped or ())

    def fold_completed(self, node_id: str) -> None:
        self.completed.add(node_id)

    def get(self, run_id: str):
        return SimpleNamespace(
            completed_nodes=set(self.completed),
            skipped_nodes=set(self.skipped),
        )


async def _handle(run: WorkflowRun, workflow: Workflow, task: Task, view=None):
    return await MergeNodeHandler(run_context_view_store=view).handle(
        node=workflow.get_node(MERGE), run=run, workflow=workflow,
        topology=GraphTopology.from_workflow(workflow),
        completed_task=task, task_factory=TaskFactory(),
    )


class TestMergeQuorumAcrossReloads:
    async def test_wait_all_still_fires_when_each_arrival_reloads_the_run(self):
        """The real sequence: A completes in one call frame, B in the next, and
        the run is re-fetched in between. Nothing carries the first arrival
        across that boundary any more."""
        workflow = _workflow("all")
        view = ProjectorDouble()

        view.fold_completed(PRED_A)
        first = await _handle(_reloaded_run(), workflow, _arrival(PRED_A), view)
        assert first.completion_mode == "deferred", "one of two must not fire"

        # Second arrival, fresh aggregate — the worker's next ContinueRunAction.
        view.fold_completed(PRED_B)
        second = await _handle(_reloaded_run(), workflow, _arrival(PRED_B), view)

        assert second.completion_mode == "immediate", (
            "MERGE never reached quorum: the buffer is ephemeral and the "
            "completed_nodes seed that used to rebuild it is empty since "
            "DROP CONTEXT, so each arrival counts only itself"
        )

    async def test_wait_all_fires_when_both_arrivals_share_one_frame(self):
        """Control. Same two arrivals against ONE run object — the shape every
        existing merge test uses. It passes with or without the bug, which is
        why the suite never caught this."""
        workflow = _workflow("all")
        run = _reloaded_run()

        first = await _handle(run, workflow, _arrival(PRED_A))
        assert first.completion_mode == "deferred"
        second = await _handle(run, workflow, _arrival(PRED_B))
        assert second.completion_mode == "immediate"

    async def test_wait_any_is_unaffected(self):
        """``any`` fires on the first arrival, so it never depended on state
        surviving a reload. Pins the boundary of the damage."""
        workflow = _workflow("any")
        result = await _handle(_reloaded_run(), workflow, _arrival(PRED_A))
        assert result.completion_mode == "immediate"

    async def test_a_predecessor_known_only_to_the_projector_counts(self):
        """A predecessor that completed in an earlier frame is recorded in the
        projector, not on the reloaded aggregate. Quorum has to see it."""
        workflow = _workflow("all")
        view = ProjectorDouble(completed={PRED_A, PRED_B})
        result = await _handle(_reloaded_run(), workflow, _arrival(PRED_B), view)
        assert result.completion_mode == "immediate", (
            "PRED_A completed earlier and is in the projector; only the "
            "aggregate is empty"
        )

    async def test_no_inconsistent_state_warning_when_both_are_known(self, caplog):
        """The 'neither completed nor skipped' warning at merge_handler:208 is
        the handler telling us it could not classify a predecessor."""
        workflow = _workflow("all")
        view = ProjectorDouble()
        with caplog.at_level(logging.WARNING):
            view.fold_completed(PRED_A)
            await _handle(_reloaded_run(), workflow, _arrival(PRED_A), view)
            view.fold_completed(PRED_B)
            await _handle(_reloaded_run(), workflow, _arrival(PRED_B), view)
        assert not [
            r for r in caplog.records
            if "neither completed nor skipped" in r.message
        ], "MERGE could not classify a predecessor it should know about"
