"""HUMAN_ACTION confirm/reject routes exactly one branch.

Regression: edges authored with the legacy ``condition="confirm"/"reject"``
labels (no ``source_port_id``) passed ``compute_fanout``'s port filter as
"port-less" on the resume path, so BOTH branches fired — and the taken edge
un-skipped the opposite branch under SA-846 taken-edge semantics. The resume
use case classifies the exact taken-edge set into
``run.context.last_taken_edge_ports``; ``process_successors`` now fans out
over that set when it exists.

Matrix pinned here:
  * port-id edges (canonical FE shape)        → chosen branch only
  * condition-labeled edges (legacy shape)    → chosen branch only  [the bug]
  * unlabeled edges                           → both continue (documented
    fallback so a single unlabeled output always proceeds)
  * mixed-case ``_action`` ("Confirm")        → classifies as confirm
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
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskCommand,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


def _workflow(edge_style: str) -> Workflow:
    """TRIGGER → HUMAN_ACTION → [confirm] branch-a / [reject] branch-b."""
    if edge_style == "ports":
        e_a = WorkflowEdge(id="e-a", source_node_id="ha", target_node_id="branch-a",
                           source_port_id="confirm")
        e_b = WorkflowEdge(id="e-b", source_node_id="ha", target_node_id="branch-b",
                           source_port_id="reject")
    elif edge_style == "condition":
        e_a = WorkflowEdge(id="e-a", source_node_id="ha", target_node_id="branch-a",
                           condition="confirm")
        e_b = WorkflowEdge(id="e-b", source_node_id="ha", target_node_id="branch-b",
                           condition="reject")
    else:  # unlabeled
        e_a = WorkflowEdge(id="e-a", source_node_id="ha", target_node_id="branch-a")
        e_b = WorkflowEdge(id="e-b", source_node_id="ha", target_node_id="branch-b")
    return Workflow(
        id=f"wf-ha-{edge_style}", name="ha-branch-routing",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="ha", name="approve", node_type=NodeType.HUMAN_ACTION,
                         config={"instruction": "ok?", "input_schema": []}),
            WorkflowNode(id="branch-a", name="onConfirm", node_type=NodeType.COMPUTE,
                         config={"expression": "1"}),
            WorkflowNode(id="branch-b", name="onReject", node_type=NodeType.COMPUTE,
                         config={"expression": "2"}),
        ],
        edges=[
            WorkflowEdge(id="e0", source_node_id="trig", target_node_id="ha"),
            e_a, e_b,
        ],
    )


async def _run_to_resume(container, edge_style: str, action: str):
    """Start the workflow, resume the pending HA with *action*, drain."""
    container["_infra"].repos.workflow.save(_workflow(edge_style))
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=f"wf-ha-{edge_style}", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    run_id = result.id
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()

    task_repo = container["_infra"].repos.task
    pending = [
        t for t in task_repo.list_by_run(run_id)
        if t.node_id == "ha" and t.status == TaskStatus.PENDING
    ]
    assert len(pending) == 1, f"expected one pending HA task, got {len(pending)}"
    await container["resume_pending_task"].execute(ResumePendingTaskCommand(
        run_id=run_id, task_id=pending[0].id, updated_input={"_action": action},
    ))
    await QueueRunner(engine, queue).run_until_idle()
    return run_id


def _branch_tasks(container, run_id: str) -> dict[str, list[str]]:
    statuses: dict[str, list[str]] = {}
    for t in container["_infra"].repos.task.list_by_run(run_id):
        statuses.setdefault(t.node_id, []).append(t.status.value)
    return statuses


def _assert_single_branch(container, run_id: str, taken: str, not_taken: str) -> None:
    statuses = _branch_tasks(container, run_id)
    assert statuses.get(taken) == ["COMPLETED"], (
        f"taken branch {taken} should have run once: {statuses.get(taken)}"
    )
    assert not_taken not in statuses, (
        f"opposite branch {not_taken} must not get a task: {statuses.get(not_taken)}"
    )
    run = container["_infra"].repos.run.get_by_id(run_id)
    assert not_taken in run.context.skipped_nodes
    assert run.status == RunStatus.COMPLETED


@pytest.mark.parametrize("edge_style", ["ports", "condition"])
@pytest.mark.asyncio
async def test_confirm_runs_only_confirm_branch(container, edge_style):
    run_id = await _run_to_resume(container, edge_style, "confirm")
    _assert_single_branch(container, run_id, taken="branch-a", not_taken="branch-b")


@pytest.mark.parametrize("edge_style", ["ports", "condition"])
@pytest.mark.asyncio
async def test_reject_runs_only_reject_branch(container, edge_style):
    run_id = await _run_to_resume(container, edge_style, "reject")
    _assert_single_branch(container, run_id, taken="branch-b", not_taken="branch-a")


@pytest.mark.asyncio
async def test_mixed_case_action_classifies_as_confirm(container):
    run_id = await _run_to_resume(container, "ports", "Confirm")
    _assert_single_branch(container, run_id, taken="branch-a", not_taken="branch-b")


@pytest.mark.asyncio
async def test_unlabeled_edges_continue_on_both(container):
    """Documented fallback: edges with neither port id nor condition label
    are always taken, so a single-unlabeled-output HA never stalls."""
    run_id = await _run_to_resume(container, "unlabeled", "confirm")
    statuses = _branch_tasks(container, run_id)
    assert statuses.get("branch-a") == ["COMPLETED"]
    assert statuses.get("branch-b") == ["COMPLETED"]
