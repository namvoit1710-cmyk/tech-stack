"""Tests for OutputNodeHandler."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import NodeType, TaskStatus
from app.layer2_application.node_handlers.output_handler import (
    OutputNodeHandler,
    _build_type_map_from_schema,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer1_domain.exceptions import ExpressionError


def _fields(result, node_id: str) -> dict:
    """Resolved OUTPUT fields, unwrapped from the node-id-keyed entry."""
    return result.state_updates[node_id]["output"]


@pytest.fixture
def variable_resolver():
    return VariableResolver(strategies=[TraversalResolverStrategy()])


@pytest.fixture
def task_factory():
    return TaskFactory()


@pytest.fixture
def handler(variable_resolver):
    return OutputNodeHandler(variable_resolver)


@pytest.fixture
def output_node():
    return WorkflowNode(
        id="output-1",
        name="Workflow Output",
        node_type=NodeType.OUTPUT,
        config={
            "output_schema": [
                {"key": "result", "type": "string", "label": "Result"},
                {"key": "total", "type": "number", "label": "Total"},
            ],
        },
    )


@pytest.fixture
def output_node_with_binding():
    return WorkflowNode(
        id="output-2",
        name="Final Output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "result": "{{$process.data.summary}}",
            "total": "{{$process.data.count}}",
        },
        config={
            "output_schema": [
                {"key": "result", "type": "string"},
                {"key": "total", "type": "number"},
            ],
        },
    )


@pytest.fixture
def run():
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "process": {"data": {"summary": "Completed successfully", "count": 42}},
                "trigger": {"body": {"name": "Alice"}},
            },
        ),
    )


@pytest.fixture
def topology():
    return GraphTopology()


@pytest.fixture
def workflow(output_node):
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[output_node],
        edges=[],
    )


# ────────────── Basic Handling ──────────────


@pytest.mark.asyncio
async def test_output_handler_returns_immediate_completion(
    handler, output_node, run, workflow, topology, task_factory
):
    """OUTPUT node completes immediately without user interaction."""
    result = await handler.handle(
        node=output_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert result.completion_mode == "immediate"
    assert result.new_task_status == "COMPLETED"


@pytest.mark.asyncio
async def test_output_handler_no_tasks_dispatched(
    handler, output_node, run, workflow, topology, task_factory
):
    """OUTPUT node does not dispatch any tasks (inline execution)."""
    result = await handler.handle(
        node=output_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert result.tasks_to_dispatch == []


# ────────────── Input Binding Resolution ──────────────


@pytest.mark.asyncio
async def test_output_handler_resolves_input_binding(
    handler, output_node_with_binding, run, workflow, topology, task_factory
):
    """OUTPUT node resolves input_binding expressions."""
    result = await handler.handle(
        node=output_node_with_binding, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-2") == {
        "result": "Completed successfully",
        "total": 42,
    }


@pytest.mark.asyncio
async def test_output_handler_empty_binding_produces_empty_output(
    handler, output_node, run, workflow, topology, task_factory
):
    """OUTPUT node with no input_binding produces an empty fields dict."""
    result = await handler.handle(
        node=output_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-1") == {}


# ────────────── Type Coercion ──────────────


@pytest.mark.asyncio
async def test_output_handler_coerces_types_from_schema(handler, task_factory):
    """OUTPUT node coerces values based on output_schema field types."""
    node = WorkflowNode(
        id="output-typed",
        name="Typed Output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "count": "{{$data.count_str}}",
            "active": "{{$data.active_str}}",
        },
        config={
            "output_schema": [
                {"key": "count", "type": "number"},
                {"key": "active", "type": "boolean"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "data": {"count_str": "123", "active_str": "true"},
            },
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-typed")["count"] == 123
    assert _fields(result, "output-typed")["active"] is True


# ────────────── Empty/Minimal Config ──────────────


@pytest.mark.asyncio
async def test_output_handler_minimal_config(handler, task_factory):
    """OUTPUT handler works with minimal/empty config."""
    node = WorkflowNode(
        id="output-min",
        name="Min Output",
        node_type=NodeType.OUTPUT,
        config={},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert result.completion_mode == "immediate"
    assert _fields(result, "output-min") == {}


@pytest.mark.asyncio
async def test_output_handler_schema_as_json_string(handler, task_factory):
    """OUTPUT handler parses output_schema from JSON string."""
    node = WorkflowNode(
        id="output-json",
        name="JSON Schema Output",
        node_type=NodeType.OUTPUT,
        input_binding={"name": "{{$data.name}}"},
        config={
            "output_schema": '[{"key": "name", "type": "string"}]',
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(variables={"data": {"name": "Test"}}),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-json") == {"name": "Test"}


# ────────────── Error Handling ──────────────


@pytest.mark.asyncio
async def test_output_handler_invalid_expression_raises(handler, task_factory):
    """OUTPUT handler raises ExpressionError for invalid expressions."""
    node = WorkflowNode(
        id="output-err",
        name="Error Output",
        node_type=NodeType.OUTPUT,
        input_binding={"value": "{{$nonexistent.path}}"},
        config={
            "output_schema": [{"key": "value", "type": "string"}],
        },
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(variables={}))

    with pytest.raises(ExpressionError):
        await handler.handle(
            node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
            topology=GraphTopology(), completed_task=None, task_factory=task_factory,
        )


# ────────────── Complex Data Structures ──────────────


@pytest.mark.asyncio
async def test_output_handler_with_nested_data(handler, task_factory):
    """OUTPUT handler resolves nested object references."""
    node = WorkflowNode(
        id="output-nested",
        name="Nested Output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "user_name": "{{$result.user.name}}",
            "user_email": "{{$result.user.email}}",
            "items": "{{$result.items}}",
        },
        config={
            "output_schema": [
                {"key": "user_name", "type": "string"},
                {"key": "user_email", "type": "string"},
                {"key": "items", "type": "array"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "result": {
                    "user": {"name": "John", "email": "john@example.com"},
                    "items": [{"id": 1}, {"id": 2}],
                },
            },
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-nested")["user_name"] == "John"
    assert _fields(result, "output-nested")["user_email"] == "john@example.com"
    assert _fields(result, "output-nested")["items"] == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_output_handler_preserves_object_type(handler, task_factory):
    """OUTPUT handler preserves object types when type is 'object'."""
    node = WorkflowNode(
        id="output-obj",
        name="Object Output",
        node_type=NodeType.OUTPUT,
        input_binding={
            "metadata": "{{$data.meta}}",
        },
        config={
            "output_schema": [
                {"key": "metadata", "type": "object"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "data": {"meta": {"version": "1.0", "tags": ["a", "b"]}},
            },
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="T", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert _fields(result, "output-obj")["metadata"] == {"version": "1.0", "tags": ["a", "b"]}


# ────────────── Extract Type Map ──────────────


class TestExtractTypeMap:
    def test_extracts_types_from_schema(self, handler):
        schema = [
            {"key": "name", "type": "string"},
            {"key": "count", "type": "number"},
            {"key": "active", "type": "boolean"},
        ]
        type_map = _build_type_map_from_schema(schema)

        assert type_map == {
            "name": "string",
            "count": "number",
            "active": "boolean",
        }

    def test_defaults_to_string(self, handler):
        schema = [{"key": "unknown"}]  # no type specified
        type_map = _build_type_map_from_schema(schema)

        assert type_map == {"unknown": "string"}

    def test_skips_invalid_entries(self, handler):
        schema = [
            {"key": "valid", "type": "string"},
            "not a dict",
            {"no_key": "value"},
            None,
        ]
        type_map = _build_type_map_from_schema(schema)

        assert type_map == {"valid": "string"}

    def test_empty_schema(self, handler):
        assert _build_type_map_from_schema([]) == {}
        assert _build_type_map_from_schema(None) == {}
