"""Child-workflow failure propagation: a parent WORKFLOW node must surface the
child's REAL error (not ``unknown``) and name the child workflow + failing node
rather than their UUIDs.

Regression for two gaps observed as
``Child workflow '7d57f1d0-...' failed: unknown``:

  * ``check_run_completion`` marked a run FAILED without ever setting
    ``run.error`` — a parent reading ``child_run.error`` saw ``unknown``.
  * the propagated message used ``child_run.workflow_id`` (a UUID) instead of
    the child workflow's name, and never named the failing node.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


def _child_workflow() -> Workflow:
    """TRIGGER → TASK('HTTP Request'). The TASK awaits a worker callback so we
    can fail it explicitly with an HTTP-400-shaped error."""
    return Workflow(
        id="child-wf", name="Order Sync",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="child-task", name="HTTP Request",
                node_type=NodeType.TASK, worker_type="http-request-worker",
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="child-trig", target_node_id="child-task")],
    )


def _parent_workflow() -> Workflow:
    """TRIGGER → WORKFLOW(child, on_failure=fail)."""
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="wf-node", name="Define Template Trigger",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_child_failure_forwards_real_error_and_names(container):
    repo = container["_infra"].repos.workflow
    repo.save(_child_workflow())
    repo.save(_parent_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task

    parent_run = run_repo.get_by_id(parent_run_id)
    child_run_id = parent_run.context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    # The child's TASK is dispatched and awaiting a worker callback.
    child_tasks = [t for t in task_repo.list_by_run(child_run_id) if t.node_id == "child-task"]
    assert child_tasks, "child TASK was not dispatched"
    child_task = child_tasks[0]
    assert child_task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}

    # Worker reports an HTTP 400 (the enriched http-request-worker error shape).
    http_error = (
        "HTTP 400 for POST https://api.example.com/orders "
        '— {"detail": "bad request"}'
    )
    fail_result = await container["fail_task"].execute(
        FailTaskCommand(
            run_id=child_run_id,
            task_id=child_task.id,
            error={"code": "WORKER_ERROR", "message": http_error},
        )
    )
    assert fail_result.failed, f"fail_task did not fail: {fail_result.error}"
    await _drain(container)

    # 1. Child run carries the real cause + the failing node's NAME (not 'unknown').
    child_run = run_repo.get_by_id(child_run_id)
    assert child_run.status == RunStatus.FAILED
    assert child_run.error, "child run error was not set (would propagate as 'unknown')"
    assert "unknown" not in child_run.error
    assert "HTTP 400" in child_run.error
    assert "HTTP Request" in child_run.error  # failing node NAME, not its UUID

    # 2. Parent WORKFLOW tracking task names the child workflow + carries the error.
    tracking = next(
        t for t in task_repo.list_by_run(parent_run_id) if t.node_id == "wf-node"
    )
    assert "unknown" not in (tracking.error or "")
    assert "Order Sync" in tracking.error   # child workflow NAME …
    assert "child-wf" not in tracking.error  # … not its UUID
    assert "HTTP 400" in tracking.error
    assert "HTTP Request" in tracking.error

    # 3. Parent run failed and its error carries the same propagated detail.
    parent_run = run_repo.get_by_id(parent_run_id)
    assert parent_run.status == RunStatus.FAILED
    assert "HTTP 400" in (parent_run.error or "")
    assert "Order Sync" in (parent_run.error or "")
