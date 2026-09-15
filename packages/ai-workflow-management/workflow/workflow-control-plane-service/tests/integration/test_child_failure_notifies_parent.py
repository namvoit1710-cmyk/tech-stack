"""Regression: a child run that dies inside ``orchestrate_execute_node`` must
fail its parent, not hang it.

There are two terminal paths for the same class of failure and, before this
fix, only one of them told the parent anything:

    orchestrate_execute_node's own ``except``
        CAS-saved FAILED + emit_run_status, and stopped there.
        No ``publish_callback``. No ``fail_parent_tracking_task``.

    OrchestrationWorker._force_fail_run_safely -> engine.force_fail_run
        the same terminal transition PLUS both notifications.

Measured on tenant-1 2026-08-25, six runs of the sub-workflow repro, and the
child's error string said which path had run:

    bare  "Run 'x': expected version N, found N+1"   -> parent RUNNING forever  2/2
    "Orchestration failed while processing continue" -> parent FAILED           3/3
    (no error)                                       -> parent COMPLETED        1/1

Nothing rescued the hung ones. ``SweepTimedOutChildRunsUseCase`` enumerates
``statuses=[RUNNING]`` child runs, so an already-FAILED child is invisible to
it, and it skips any WORKFLOW node whose ``timeout_ms`` is unset — the default
for every ``builtin:workflow`` node built in the UI. One observed parent sat
RUNNING for 20 minutes until a human cancelled it.

The fault injected below is the real one: ``OptimisticConcurrencyError`` raised
out of ``_execute_node_inner``. See ``test_parallel_completion_cas_race.py``
for why that was happening in the first place.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
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
    return Workflow(
        id="child-wf", name="Child",
        nodes=[
            WorkflowNode(id="c-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="c-task", name="Child work",
                node_type=NodeType.TASK, worker_type="http-worker",
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="c-trig", target_node_id="c-task")],
    )


def _parent_workflow() -> Workflow:
    return Workflow(
        id="parent-wf", name="Parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="wf-node", name="Call child",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_child_dying_inside_execute_node_fails_the_parent(container):
    wf_repo = container["_infra"].repos.workflow
    wf_repo.save(_child_workflow())
    wf_repo.save(_parent_workflow())

    engine = container["_orchestration_worker"]._engine
    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task

    # Fire once, on the first node the CHILD executes — exactly where the
    # trailing ``asave(run)`` blew up in production.
    real_inner = engine._execute_node_inner
    fired: dict[str, bool] = {"yes": False}

    async def _explode_once(run_id: str, task_id: str) -> None:
        run = run_repo.get_by_id(run_id)
        if run is not None and run.parent_run_id and not fired["yes"]:
            fired["yes"] = True
            raise OptimisticConcurrencyError(run_id, expected=7, actual=8)
        return await real_inner(run_id, task_id)

    engine._execute_node_inner = _explode_once

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={}),
    )
    assert not result.error, result.error
    parent_run_id = result.id

    await _drain(container)

    assert fired["yes"], "the fault never fired — the test proves nothing"

    parent_run = run_repo.get_by_id(parent_run_id)
    child_run_id = parent_run.context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    child_run = run_repo.get_by_id(child_run_id)
    assert child_run.status is RunStatus.FAILED, (
        f"the child should be terminal, got {child_run.status}"
    )

    # The whole point. Before the fix this stayed RUNNING with no timeout, no
    # sweeper, and no event that would ever move it.
    assert parent_run.status is not RunStatus.RUNNING, (
        "the parent is still RUNNING after its child died — nothing will ever "
        "move it: the child-timeout sweeper only looks at RUNNING children and "
        "only when the WORKFLOW node has an explicit timeout_ms"
    )
    assert parent_run.status is RunStatus.FAILED, (
        f"expected the parent to fail, got {parent_run.status}: {parent_run.error!r}"
    )

    tracking = [t for t in task_repo.list_by_run(parent_run_id) if t.node_id == "wf-node"]
    assert tracking, "the parent has no tracking task for the WORKFLOW node"
    assert tracking[-1].status in {
        TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED,
    }, (
        f"the parent's WORKFLOW tracking task is still {tracking[-1].status} — "
        "fail_parent_tracking_task was not called"
    )
