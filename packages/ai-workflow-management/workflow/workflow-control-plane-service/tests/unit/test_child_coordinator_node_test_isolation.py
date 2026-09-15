"""Node-by-node test isolation in ChildWorkflowCoordinator.

When a WORKFLOW node is tested node-by-node (``parent_run.test_mode == "node"``)
and its child sub-workflow completes/fails, the coordinator must mark the
WORKFLOW node done (tracking task + NodeCompleted/NodeFailed) but MUST NOT run
``process_and_dispatch_successors`` / ``check_run_completion`` — those would flip
the PERMANENT test run terminal (``check_run_completion`` runs unconditionally
inside ``process_and_dispatch_successors``).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer2_application.orchestration.child_workflow_coordinator import (
    ChildWorkflowCoordinator,
)
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus

pytestmark = pytest.mark.asyncio


def _engine():
    eng = MagicMock()
    eng._view_variables = MagicMock(return_value={})
    eng.apply_state_updates_as_files = AsyncMock()
    eng.process_and_dispatch_successors = AsyncMock()
    eng.check_run_completion = AsyncMock()
    eng.drain_events = AsyncMock()
    eng.drain_events = AsyncMock()
    eng.publish_event = MagicMock()
    # A sub-workflow node can be a loop body; the coordinator now advances the
    # parent loop after completion (no-op for a non-loop node).
    eng.maybe_enqueue_loop_iteration = AsyncMock()
    return eng


def _coord(eng, parent_run, tracking_task):
    coord = ChildWorkflowCoordinator(
        engine=eng, run_repo=MagicMock(), task_repo=AsyncMock(),
        workflow_repo=MagicMock(), workflow_snapshot_repo=MagicMock(),
    )
    # Bypass the lookup/guard chain — exercise the completion body directly.
    coord._resolve_parent_context = AsyncMock(
        return_value=(parent_run, MagicMock(), tracking_task),
    )
    return coord


def _runs(test_mode):
    parent = WorkflowRun(id="parent", workflow_id="wf", status=RunStatus.RUNNING)
    parent.test_mode = test_mode
    child = WorkflowRun(id="child", workflow_id="child-wf",
                        status=RunStatus.COMPLETED)
    child.parent_run_id = "parent"
    child.parent_node_id = "wf-node"
    child.output_data = {}
    tracking = Task(id="parent-wf-node", run_id="parent", node_id="wf-node",
                    status=TaskStatus.RUNNING)
    return parent, child, tracking


async def test_complete_parent_node_test_skips_successors_and_completion():
    eng = _engine()
    parent, child, tracking = _runs("node")
    coord = _coord(eng, parent, tracking)

    await coord.complete_parent(child)

    # WORKFLOW node IS marked done...
    assert tracking.status == TaskStatus.COMPLETED
    assert eng.publish_event.called  # NodeCompleted + ChildRunCompleted
    # ...but the permanent test run is NOT advanced/completed.
    eng.process_and_dispatch_successors.assert_not_awaited()
    eng.drain_events.assert_awaited()
    eng.drain_events.assert_awaited()
    assert parent.status == RunStatus.RUNNING  # not flipped


async def test_complete_parent_production_still_dispatches():
    eng = _engine()
    parent, child, tracking = _runs("")  # production run
    coord = _coord(eng, parent, tracking)

    await coord.complete_parent(child)

    assert tracking.status == TaskStatus.COMPLETED
    eng.process_and_dispatch_successors.assert_awaited_once()
