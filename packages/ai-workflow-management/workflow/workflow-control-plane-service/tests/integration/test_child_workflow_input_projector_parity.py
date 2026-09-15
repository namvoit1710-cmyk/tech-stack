"""Child run gets the same projector input parity a top-level run gets.

``StartRunUseCase`` lands a top-level run's input in the event log /
projector via two events:

  * ``RunStatusChanged(status="started")`` → projector ``input_data``
  * ``NodeOutputProduced(__bootstrap__)``  → projector ``variables.input``

``ChildWorkflowCoordinator.start()`` historically wrote the child's input
only to the aggregate, so a view-based read of a child run saw neither
field — ``$input`` was invisible through the projector even though it
resolved fine off the aggregate. These tests pin the parity: a child
run's projector view must carry ``input_data`` AND ``variables.input``.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer2_application.resolve.variable_resolver import (
    set_current_run_id,
    reset_current_run_id,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
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
    """TRIGGER → COMPUTE — completes fully, no pending wait."""
    return Workflow(
        id="child-wf", name="child",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="child-compute", name="cc", node_type=NodeType.COMPUTE,
                config={"expression": "21 + 21"},
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="child-trig", target_node_id="child-compute")],
    )


def _parent_workflow() -> Workflow:
    """TRIGGER → WORKFLOW(child-wf). ``input_binding={}`` so the child
    inherits the parent's whole input via the merge in ``start()``."""
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="wf-node", name="wn", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_child_run_projector_carries_input_data_and_variables(container):
    repo = container["_infra"].repos.workflow
    repo.save(_child_workflow())
    repo.save(_parent_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={"value": 23})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    child_run_id = run_repo.get_by_id(parent_run_id).context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    child_view = container["run_context_view_store"].get(child_run_id)
    assert child_view is not None, "no projector view for child run"

    # input_data parity ← RunStatusChanged(status="started").
    assert child_view.input_data == {"value": 23}, (
        f"child projector input_data not seeded; got {child_view.input_data!r}"
    )

    # variables.input parity ← NodeOutputProduced(__bootstrap__).
    assert "input" in child_view.variables, (
        f"child projector variables lacks 'input'; keys: {sorted(child_view.variables)}"
    )


@pytest.mark.asyncio
async def test_child_input_resolves_through_projector_view(container):
    """``$input`` must resolve off the child's PROJECTOR view, not only the
    aggregate — this is what breaks if resolution ever goes view-authoritative.
    """
    repo = container["_infra"].repos.workflow
    repo.save(_child_workflow())
    repo.save(_parent_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={"value": 23})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    child_run_id = run_repo.get_by_id(parent_run_id).context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    child_view = container["run_context_view_store"].get(child_run_id)
    resolver = container["_orchestration_worker"]._engine._variable_resolver
    tok = set_current_run_id(child_run_id)
    try:
        val = await resolver.resolve("{{$input.value}}", dict(child_view.variables))
        assert val == 23, f"$input.value via projector view = {val!r}"
    finally:
        reset_current_run_id(tok)
