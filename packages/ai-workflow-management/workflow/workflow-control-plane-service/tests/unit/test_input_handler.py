"""Tests for InputNodeHandler."""

import json
import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import NodeType, TaskStatus
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer1_domain.events import InputRequested


class _MockDataStoreService:
    """Minimal data store that returns pre-set data for materialize calls."""

    def __init__(self, data_by_file_id: dict | None = None):
        self._data = data_by_file_id or {}

    async def materialize(self, file_ref: dict, max_bytes: int = 0) -> dict:
        file_id = file_ref.get("file_id", "")
        return self._data.get(file_id, {})

    async def materialize_guarded(self, file_ref, *, max_bytes=None, context=""):
        # SA-1942 — mirror the real contract: guard-off → plain materialize.
        return await self.materialize(file_ref)


@pytest.fixture
def variable_resolver():
    return VariableResolver(strategies=[TraversalResolverStrategy()])


@pytest.fixture
def task_factory():
    return TaskFactory()


@pytest.fixture
def handler(variable_resolver):
    return InputNodeHandler(variable_resolver)


@pytest.fixture
def input_node():
    return WorkflowNode(
        id="input-1",
        name="Collect Info",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [
                {"key": "name", "type": "string", "label": "Full Name", "required": True},
                {"key": "email", "type": "string", "label": "Email"},
            ],
            "instruction": "Please fill in your details.",
        },
    )


@pytest.fixture
def input_node_with_binding():
    return WorkflowNode(
        id="input-2",
        name="Verify Data",
        node_type=NodeType.INPUT,
        input_binding={"prefilled_name": "{{$trigger.body.name}}"},
        config={
            "input_schema": [{"key": "name", "type": "string"}],
            "instruction": "Verify the data.",
        },
    )


@pytest.fixture
def run():
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "trigger": {"body": {"name": "Alice"}},
            },
        ),
    )


@pytest.fixture
def topology():
    return GraphTopology()


@pytest.fixture
def workflow(input_node):
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[input_node],
        edges=[],
    )


@pytest.mark.asyncio
async def test_creates_pending_task(handler, input_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.worker_type is None
    assert task.node_id == "input-1"


@pytest.mark.asyncio
async def test_stores_schema_in_metadata(handler, input_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata is not None
    assert task.output_metadata["node_type"] == "INPUT"
    assert len(task.output_metadata["input_schema"]) == 2
    assert task.output_metadata["instruction"] == "Please fill in your details."


@pytest.mark.asyncio
async def test_resolves_input_binding(handler, input_node_with_binding, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=input_node_with_binding, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input == {"prefilled_name": "Alice"}
    assert task.output_metadata["prefilled"] == {"prefilled_name": "Alice"}


@pytest.mark.asyncio
async def test_emits_input_requested_event(handler, input_node, run, workflow, topology, task_factory):
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.run_id == "run-1"
    assert event.node_id == "input-1"
    assert len(result.tasks_to_dispatch[0].output_metadata["input_schema"]) == 2


@pytest.mark.asyncio
async def test_no_worker_id_required(handler, input_node, run, workflow, topology, task_factory):
    """INPUT nodes do not require a worker_type, unlike TASK nodes."""
    input_node.worker_type = None
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch) == 1
    assert result.tasks_to_dispatch[0].worker_type is None


@pytest.mark.asyncio
async def test_empty_config(handler, task_factory):
    """Handler works with minimal config."""
    node = WorkflowNode(id="input-min", name="Min", node_type=NodeType.INPUT, config={})
    run = WorkflowRun(id="run-1", workflow_id="wf-1")
    topology = GraphTopology()

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=topology, completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["input_schema"] == []
    assert task.output_metadata["instruction"] == ""


@pytest.mark.asyncio
async def test_provisions_header_only_root_without_scalars(
    variable_resolver, run, topology, task_factory,
):
    """INPUT must always get a non-empty ``__root__.file_id`` (uniform with
    HUMAN_ACTION / FILE_UPLOAD) even when the schema declares no scalars or
    tables — the handler forces a header-only ``__row_id`` root."""
    from app.layer4_frameworks.clients.mock_file_service_client import (
        MockFileServiceClient,
    )
    fs = MockFileServiceClient()
    handler = InputNodeHandler(variable_resolver, file_service_client=fs)
    node = WorkflowNode(
        id="input-empty",
        name="Empty Input",
        node_type=NodeType.INPUT,
        config={"input_schema": [], "instruction": "x"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])

    result = await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    working = result.state_updates.get("input-empty")
    assert working is not None and working.get("__file_ref") is True
    assert working.get("file_id"), "root file_id must be non-empty"
    assert working.get("artifact_type") == "object_field"
    assert working.get("columns") == ["__row_id"]


@pytest.mark.asyncio
async def test_no_provisioning_without_file_client(
    handler, run, topology, task_factory,
):
    """Without a wired file_client, INPUT still skips provisioning entirely —
    force_root only applies once a client exists."""
    node = WorkflowNode(
        id="input-empty2", name="Empty", node_type=NodeType.INPUT,
        config={"input_schema": [], "instruction": "x"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    result = await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )
    assert "input-empty2" not in result.state_updates


@pytest.mark.asyncio
async def test_type_coercion_from_input_schema(handler, task_factory):
    """Resolved values are coerced based on input_schema field types."""
    node = WorkflowNode(
        id="input-typed",
        name="Typed Input",
        node_type=NodeType.INPUT,
        input_binding={"age": "{{$trigger.body.age}}", "active": "{{$trigger.body.active}}"},
        config={
            "input_schema": [
                {"key": "age", "type": "number", "label": "Age"},
                {"key": "active", "type": "boolean", "label": "Active"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={"trigger": {"body": {"age": "25", "active": "true"}}},
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["age"] == 25
    assert isinstance(task.resolved_input["age"], (int, float))
    assert task.resolved_input["active"] is True


@pytest.mark.asyncio
async def test_type_coercion_from_output_type(handler, task_factory):
    """Auto-form format (outputType) is also supported for type derivation."""
    node = WorkflowNode(
        id="input-auto",
        name="Auto Form",
        node_type=NodeType.INPUT,
        input_binding={"count": "{{$trigger.body.count}}"},
        config={
            "input_schema": [
                {"key": "count", "outputType": "number"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={"trigger": {"body": {"count": "42"}}},
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert result.tasks_to_dispatch[0].resolved_input["count"] == 42


@pytest.mark.asyncio
async def test_explicit_input_types_takes_precedence(handler, task_factory):
    """Explicit input_types config overrides input_schema-derived types."""
    node = WorkflowNode(
        id="input-override",
        name="Override",
        node_type=NodeType.INPUT,
        input_binding={"value": "{{$trigger.body.value}}"},
        config={
            "input_schema": [{"key": "value", "type": "number"}],
            "input_types": {"value": "string"},
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={"trigger": {"body": {"value": 42}}},
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    # input_types says "string", so 42 becomes "42"
    assert result.tasks_to_dispatch[0].resolved_input["value"] == "42"


@pytest.mark.asyncio
async def test_resolve_schema_defaults(handler, task_factory):
    """Expressions in input_schema default values are resolved."""
    node = WorkflowNode(
        id="input-defaults",
        name="Defaults",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [
                {"key": "name", "type": "string", "default": "{{$trigger.body.name}}"},
                {"key": "city", "type": "string", "default": "Berlin"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={"trigger": {"body": {"name": "Alice"}}},
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert schema[0]["default"] == "Alice"
    assert schema[1]["default"] == "Berlin"


@pytest.mark.asyncio
async def test_resolve_autoform_control_props_value(handler, task_factory):
    """Expressions in auto-form fieldConfig.controlProps.value are resolved."""
    node = WorkflowNode(
        id="input-autoform",
        name="AutoForm Defaults",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [
                {
                    "key": "email",
                    "outputType": "string",
                    "fieldConfig": {
                        "controlProps": {"value": "{{$trigger.body.email}}"},
                    },
                },
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={"trigger": {"body": {"email": "alice@example.com"}}},
        ),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert schema[0]["fieldConfig"]["controlProps"]["value"] == "alice@example.com"


@pytest.mark.asyncio
async def test_input_schema_as_json_string(handler, task_factory):
    """input_schema stored as a JSON string (from CodeControl) is parsed correctly."""
    schema_list = [
        {"key": "name", "type": "string", "label": "Name"},
        {"key": "age", "type": "number", "label": "Age"},
    ]
    node = WorkflowNode(
        id="input-json",
        name="JSON Schema",
        node_type=NodeType.INPUT,
        config={"input_schema": json.dumps(schema_list)},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch[0].output_metadata["input_schema"]) == 2


@pytest.mark.asyncio
async def test_resolve_from_file_backed_context(task_factory):
    """Expressions resolve correctly when context contains file references."""
    store = _MockDataStoreService(data_by_file_id={
        "file-abc": {"output": {"city": "Berlin", "country": "Germany"}},
    })
    # Wire FileBackedResolverStrategy so the resolver navigates file_refs the
    # same way production does — no-``extracted`` model: the pointer-only
    # file_ref is resolved by materializing via the mock store on demand.
    resolver = VariableResolver(
        strategies=[FileBackedResolverStrategy(store), TraversalResolverStrategy()],
    )
    handler = InputNodeHandler(resolver)

    file_ref = build_file_ref(
        file_id="file-abc",
        row_count=1,
        columns=["output.city", "output.country"],
    )
    node = WorkflowNode(
        id="input-file",
        name="File Input",
        node_type=NodeType.INPUT,
        input_binding={"location": "{{$prev_node.output.city}}"},
        config={
            "input_schema": [{"key": "location", "type": "string"}],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(variables={"prev_node": file_ref}),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert result.tasks_to_dispatch[0].resolved_input["location"] == "Berlin"


@pytest.mark.asyncio
async def test_resolve_defaults_from_file_backed_context(task_factory):
    """Schema default values resolve from file-backed context."""
    store = _MockDataStoreService(data_by_file_id={
        "file-xyz": {"output": {"name": "Bob"}},
    })
    resolver = VariableResolver(
        strategies=[FileBackedResolverStrategy(store), TraversalResolverStrategy()],
    )
    handler = InputNodeHandler(resolver)

    file_ref = build_file_ref(
        file_id="file-xyz",
        row_count=1,
        columns=["output.name"],
    )
    node = WorkflowNode(
        id="input-file-default",
        name="File Defaults",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [
                {"key": "name", "type": "string", "default": "{{$upstream.output.name}}"},
            ],
        },
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(variables={"upstream": file_ref}),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert schema[0]["default"] == "Bob"


# ---------------------------------------------------------------------------
# Dynamic table schema tests
# ---------------------------------------------------------------------------


class _MockSchemaTransformClient:
    """Mock schema-transform client returning pre-set headers."""

    def __init__(self, headers: list[dict] | None = None):
        self._headers = headers or []
        self.called_with: str | None = None

    async def extract_headers(self, file_id: str) -> list[dict]:
        self.called_with = file_id
        return self._headers


class _MockFileServiceClient:
    """Mock file service client for OData queries."""

    def __init__(self, data: list[dict] | None = None):
        self._data = data or []
        self.queried_file_id: str | None = None

    async def query_file_data(self, file_id: str, query_params: dict) -> dict:
        self.queried_file_id = file_id
        return {"file_id": file_id, "total_matches": len(self._data), "data": self._data}

    async def download_file(self, file_id: str):
        return None

    async def upload_file(self, file_bytes, filename, content_type="text/csv"):
        return {}

    async def delete_file(self, file_id):
        return {}

    async def get_presigned_url(self, file_id, operation="download", expires_in=3600):
        return {}

    async def patch_file_data(self, file_id, patch_payload):
        return {}

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dynamic_table_schema_from_file_id(variable_resolver, task_factory):
    """When config has file_id and dynamic_table, extract headers and build table schema."""
    headers = [
        {"name": "id", "index": "A"},
        {"name": "name", "index": "B"},
        {"name": "email", "index": "C"},
    ]
    schema_client = _MockSchemaTransformClient(headers)
    file_client = _MockFileServiceClient()

    handler = InputNodeHandler(
        variable_resolver,
        schema_transform_client=schema_client,
        file_service_client=file_client,
    )

    node = WorkflowNode(
        id="input-dyn",
        name="Dynamic Table",
        node_type=NodeType.INPUT,
        config={"file_id": "file-123", "dynamic_table": True, "instruction": "Edit data"},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    task = result.tasks_to_dispatch[0]
    schema = task.output_metadata["input_schema"]
    assert len(schema) == 1
    table = schema[0]
    assert table["key"] == "table_data"
    assert table["outputType"] == "array"
    assert table["fieldConfig"]["fieldWrapper"] == "TableWrapper"
    # Check columns in the row template
    row_template = table["fields"][0]
    col_keys = [f["key"] for f in row_template["fields"]]
    assert col_keys == ["id", "name", "email"]
    assert schema_client.called_with == "file-123"


@pytest.mark.asyncio
async def test_dynamic_table_schema_carries_no_row_data(variable_resolver, task_factory):
    """Schema never embeds rows — the FE fetches data directly from the
    file-service via OData. ``controlProps.value`` must not appear on the
    table field, and the row-loading OData call must not be issued."""
    headers = [{"name": "product", "index": "A"}, {"name": "price", "index": "B"}]
    default_data = [
        {"product": "Widget", "price": "10"},
        {"product": "Gadget", "price": "20"},
    ]
    schema_client = _MockSchemaTransformClient(headers)
    file_client = _MockFileServiceClient(default_data)

    handler = InputNodeHandler(
        variable_resolver,
        schema_transform_client=schema_client,
        file_service_client=file_client,
    )

    node = WorkflowNode(
        id="input-dyn-data",
        name="Table With Data",
        node_type=NodeType.INPUT,
        config={"file_id": "file-456", "dynamic_table": True},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    table = result.tasks_to_dispatch[0].output_metadata["input_schema"][0]
    assert "value" not in table.get("fieldConfig", {}).get("controlProps", {})
    # Row-loading OData query is never issued — schema build stays header-only.
    assert file_client.queried_file_id is None


@pytest.mark.asyncio
async def test_dynamic_table_with_expression_file_id(variable_resolver, task_factory):
    """file_id as expression {{$prev.__file_id}} resolves from run context."""
    headers = [{"name": "col1", "index": "A"}]
    schema_client = _MockSchemaTransformClient(headers)
    file_client = _MockFileServiceClient()

    handler = InputNodeHandler(
        variable_resolver,
        schema_transform_client=schema_client,
        file_service_client=file_client,
    )

    node = WorkflowNode(
        id="input-expr",
        name="Expr File ID",
        node_type=NodeType.INPUT,
        config={"file_id": "{{$prev.file_id}}", "dynamic_table": True},
    )
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(variables={"prev": {"file_id": "resolved-file-789"}}),
    )

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    assert schema_client.called_with == "resolved-file-789"
    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert len(schema) == 1
    assert schema[0]["key"] == "table_data"


@pytest.mark.asyncio
async def test_dynamic_table_fallback_to_static_schema(variable_resolver, task_factory):
    """When extract_headers returns empty, falls back to static input_schema."""
    schema_client = _MockSchemaTransformClient(headers=[])  # empty = failure
    file_client = _MockFileServiceClient()

    handler = InputNodeHandler(
        variable_resolver,
        schema_transform_client=schema_client,
        file_service_client=file_client,
    )

    static_schema = [{"key": "fallback", "type": "string", "label": "Fallback"}]
    node = WorkflowNode(
        id="input-fallback",
        name="Fallback",
        node_type=NodeType.INPUT,
        config={
            "file_id": "file-bad",
            "dynamic_table": True,
            "input_schema": static_schema,
        },
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert len(schema) == 1
    assert schema[0]["key"] == "fallback"


@pytest.mark.asyncio
async def test_dynamic_table_skipped_without_clients(variable_resolver, task_factory):
    """When schema_transform_client is None, dynamic schema is skipped."""
    handler = InputNodeHandler(variable_resolver)  # no clients

    node = WorkflowNode(
        id="input-no-client",
        name="No Client",
        node_type=NodeType.INPUT,
        config={"file_id": "file-123", "dynamic_table": True},
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1")

    result = await handler.handle(
        node=node, run=run, workflow=Workflow(id="wf-1", name="Test", nodes=[node]),
        topology=GraphTopology(), completed_task=None, task_factory=task_factory,
    )

    # Falls through to empty static schema
    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert schema == []


@pytest.mark.asyncio
async def test_existing_static_schema_unchanged(handler, input_node, run, workflow, topology, task_factory):
    """When no file_id in config, existing static schema behavior is unchanged."""
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    schema = result.tasks_to_dispatch[0].output_metadata["input_schema"]
    assert len(schema) == 2
    assert schema[0]["key"] == "name"
    assert schema[1]["key"] == "email"


# ---------------------------------------------------------------------------
# runThrough mode tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=(
    "runThrough INPUT fast-path is not implemented in the current architecture. "
    "PendingHandlerBase.handle always creates a PENDING task; successor fan-out "
    "moved to compute_fanout (Phase 6.3), so the handler no longer returns "
    "successor tasks. This pre-refactor contract test asserts unimplemented "
    "behavior — see also stale tests/integration/test_input_node_run_through.py."
))
@pytest.mark.asyncio
async def test_run_through_skips_pending_task_and_returns_successors(handler, run, task_factory):
    """runThrough=true: no self PENDING task; successor tasks are returned."""
    input_node = WorkflowNode(
        id="input-rt",
        name="Soft Input",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [{"key": "name", "type": "string"}],
            "instruction": "Optional",
            "runThrough": True,
        },
    )
    next_node = WorkflowNode(id="next-1", name="Next", node_type=NodeType.TASK, worker_type="echo")
    workflow = Workflow(
        id="wf-1", name="Test",
        nodes=[input_node, next_node],
        edges=[WorkflowEdge(id="e1", source_node_id="input-rt", target_node_id="next-1")],
    )
    topology = GraphTopology(edge_map={"input-rt": workflow.edges})

    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    # Successor task is dispatched; no PENDING task for the INPUT node itself.
    assert len(result.tasks_to_dispatch) == 1
    successor = result.tasks_to_dispatch[0]
    assert successor.node_id == "next-1"
    assert successor.status != TaskStatus.PENDING


@pytest.mark.asyncio
async def test_run_through_still_emits_input_requested(handler, run, task_factory):
    """runThrough=true: input.requested event is still emitted."""
    input_node = WorkflowNode(
        id="input-rt2",
        name="Soft Input",
        node_type=NodeType.INPUT,
        config={
            "input_schema": [{"key": "name", "type": "string"}],
            "instruction": "Tell us your name",
            "runThrough": True,
        },
    )
    workflow = Workflow(id="wf-1", name="Test", nodes=[input_node], edges=[])
    topology = GraphTopology()

    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.node_id == "input-rt2"
    assert event.instruction == "Tell us your name"


@pytest.mark.skip(reason=(
    "runThrough INPUT fast-path is not implemented — PendingHandlerBase.handle "
    "produces a PENDING task and no inline output state_update. Pre-Phase-6.3 "
    "contract for unimplemented behavior."
))
@pytest.mark.asyncio
async def test_run_through_outputs_prefilled_values(variable_resolver, task_factory):
    """runThrough=true: input_binding-derived prefilled values become the node output."""
    handler = InputNodeHandler(variable_resolver)
    input_node = WorkflowNode(
        id="input-rt3",
        name="Soft Input",
        node_type=NodeType.INPUT,
        input_binding={"name": "{{$trigger.body.name}}"},
        config={
            "input_schema": [{"key": "name", "type": "string"}],
            "runThrough": True,
        },
    )
    workflow = Workflow(id="wf-1", name="Test", nodes=[input_node], edges=[])
    run = WorkflowRun(
        id="run-1", workflow_id="wf-1",
        context=RunContext(variables={"trigger": {"body": {"name": "Alice"}}}),
    )

    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=GraphTopology(),
        completed_task=None, task_factory=task_factory,
    )

    assert "input-rt3" in result.state_updates
    update = result.state_updates["input-rt3"]
    assert update["output"] == {"name": "Alice"}
    assert update["metadata"]["run_through"] is True


@pytest.mark.asyncio
async def test_run_through_false_keeps_pending_behavior(handler, input_node, run, workflow, topology, task_factory):
    """runThrough not set (or false) keeps the existing PENDING-pause behavior."""
    input_node.config["runThrough"] = False
    result = await handler.handle(
        node=input_node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(result.tasks_to_dispatch) == 1
    assert result.tasks_to_dispatch[0].status == TaskStatus.PENDING
    assert result.tasks_to_dispatch[0].node_id == "input-1"
    assert result.state_updates == {}
