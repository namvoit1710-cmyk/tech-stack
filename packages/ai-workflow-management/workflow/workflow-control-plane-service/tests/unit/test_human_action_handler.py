"""Tests for HumanActionNodeHandler."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import NodeType, TaskStatus
from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer1_domain.events import InputRequested


@pytest.fixture
def variable_resolver():
    return VariableResolver(strategies=[TraversalResolverStrategy()])


@pytest.fixture
def task_factory():
    return TaskFactory()


@pytest.fixture
def handler(variable_resolver):
    return HumanActionNodeHandler(variable_resolver)


@pytest.fixture
def action_node():
    return WorkflowNode(
        id="action-1",
        name="Manager Approval",
        node_type=NodeType.HUMAN_ACTION,
        config={
            "instruction": "Review the purchase order and approve or reject.",
            "input_schema": [
                {"key": "comments", "type": "string", "label": "Comments"},
            ],
            "confirm_label": "Approve",
            "reject_label": "Reject",
        },
    )


@pytest.fixture
def action_node_with_binding():
    return WorkflowNode(
        id="action-2",
        name="Verify Action",
        node_type=NodeType.HUMAN_ACTION,
        input_binding={"po_amount": "{{$trigger.body.amount}}"},
        config={
            "instruction": "Review amount and confirm.",
        },
    )


@pytest.fixture
def run():
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "trigger": {"body": {"amount": 5000}},
            },
        ),
    )


@pytest.fixture
def topology():
    return GraphTopology()


@pytest.fixture
def workflow(action_node):
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[action_node],
        edges=[],
    )


@pytest.mark.asyncio
async def test_creates_pending_task(handler, action_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=action_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.worker_type is None
    assert task.node_id == "action-1"


@pytest.mark.asyncio
async def test_stores_instruction_in_metadata(handler, action_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=action_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata is not None
    assert task.output_metadata["node_type"] == "HUMAN_ACTION"
    assert task.output_metadata["instruction"] == "Review the purchase order and approve or reject."
    assert task.output_metadata["confirm_label"] == "Approve"
    assert task.output_metadata["reject_label"] == "Reject"


@pytest.mark.asyncio
async def test_emits_human_action_requested_event(handler, action_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=action_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.run_id == "run-1"
    assert event.node_id == "action-1"
    assert event.instruction == "Review the purchase order and approve or reject."


@pytest.mark.asyncio
async def test_resolves_input_binding(handler, action_node_with_binding, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=action_node_with_binding, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input == {"po_amount": 5000}
    assert task.output_metadata["prefilled"] == {"po_amount": 5000}


@pytest.mark.asyncio
async def test_no_worker_type_required(handler, action_node, run, workflow, topology, task_factory):
    action_node.worker_type = None
    result = await handler.handle(
        node=action_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch) == 1
    assert result.tasks_to_dispatch[0].worker_type is None


@pytest.mark.asyncio
async def test_resolves_input_schema_expressions(handler, run, workflow, topology, task_factory):
    """``{{…}}`` expressions in the user-authored input_schema are resolved
    against run variables before the schema is rendered for the FE."""
    node = WorkflowNode(
        id="action-schema",
        name="Approve with prefill",
        node_type=NodeType.HUMAN_ACTION,
        config={
            "instruction": "Confirm the amount.",
            "input_schema": [
                {
                    "key": "amount",
                    "type": "number",
                    "label": "Amount",
                    "default": "{{$trigger.body.amount}}",
                },
                {"key": "note", "type": "string", "default": "static"},
            ],
        },
    )

    result = await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    # A full-match expression resolves to the underlying value (int 5000),
    # not its stringified form.
    assert schema[0]["default"] == 5000
    assert schema[1]["default"] == "static"


@pytest.mark.asyncio
async def test_resolves_instruction_expressions(handler, run, workflow, topology, task_factory):
    """``{{…}}`` in the instruction is resolved against run variables for
    both the stamped metadata and the InputRequested event."""
    node = WorkflowNode(
        id="action-instr",
        name="Approve",
        node_type=NodeType.HUMAN_ACTION,
        config={"instruction": "Approve the {{$trigger.body.amount}} order."},
    )

    result = await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["instruction"] == "Approve the 5000 order."
    assert result.events[0].instruction == "Approve the 5000 order."


@pytest.mark.asyncio
async def test_uses_node_instruction_fallback(handler, task_factory):
    """When config has no instruction, falls back to node.instruction."""
    node = WorkflowNode(
        id="action-fb",
        name="Fallback",
        node_type=NodeType.HUMAN_ACTION,
        instruction="Node-level instruction",
        config={},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")
    topology = GraphTopology()

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=topology, completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["instruction"] == "Node-level instruction"
