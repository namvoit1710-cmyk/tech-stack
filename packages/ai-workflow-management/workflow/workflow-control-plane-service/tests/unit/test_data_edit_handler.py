"""Tests for DATA_EDIT node handler."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.data_reference import (
    add_artifact,
    build_file_ref,
    register_artifacts,
)
from app.layer1_domain.events import InputRequested
from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.run_status import RunStatus


@pytest.fixture
def resolver():
    return VariableResolver(strategies=[
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])


@pytest.fixture
def handler(resolver):
    return DataEditNodeHandler(resolver)


@pytest.fixture
def task_factory():
    return TaskFactory()


def _make_topology(workflow: Workflow) -> GraphTopology:
    edge_map: dict[str, list] = {}
    adjacency: dict[str, list[str]] = {}
    for edge in workflow.edges:
        edge_map.setdefault(edge.source_node_id, []).append(edge)
        adjacency.setdefault(edge.source_node_id, []).append(edge.target_node_id)
    return GraphTopology(
        edge_map=edge_map,
        adjacency=adjacency,
        entry_nodes=[n.id for n in workflow.nodes],
    )


@pytest.mark.asyncio
async def test_data_edit_creates_pending_task_with_file_id(handler, task_factory):
    """Handler should resolve upstream file_id and create a PENDING task."""
    node = WorkflowNode(
        id="de-1",
        name="Edit Data",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$input.file_id}}",
            "instruction": "Review and edit the data",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(
                    file_id="f-upstream-001",
                    row_count=5,
                    columns=["name", "email"],
                ),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.resolved_input["file_id"] == "f-upstream-001"
    assert task.resolved_input["data_mode"] == "csv_single"
    assert task.output_metadata["node_type"] == "DATA_EDIT"
    assert task.output_metadata["file_id"] == "f-upstream-001"
    assert task.output_metadata["instruction"] == "Review and edit the data"
    # Source tracking — "input" is a special variable, not a workflow node
    assert task.output_metadata["source_node_id"] == "input"
    assert task.output_metadata["source_columns"] == ["name", "email"]
    assert task.output_metadata["source_row_count"] == 5


@pytest.mark.asyncio
async def test_data_edit_reads_canonical_config_fields(handler, task_factory):
    """NodeSpec Phase 6 regression: the declared DATA_EDIT fields live in
    ``node.config`` (flat-key API payloads + 042-migrated data). The
    handler must source them from there, not only from input_binding."""
    node = WorkflowNode(
        id="de-cfg",
        name="Edit Data",
        node_type=NodeType.DATA_EDIT,
        config={
            "file_id": "{{$input.file_id}}",
            "instruction": "Canonical config instruction",
            "readonly": True,
            "query": "$top=5",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-cfg",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(
                    file_id="f-upstream-cfg",
                    row_count=2,
                    columns=["a", "b"],
                ),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.resolved_input["file_id"] == "f-upstream-cfg"
    assert task.output_metadata["file_id"] == "f-upstream-cfg"
    assert task.output_metadata["instruction"] == "Canonical config instruction"
    assert task.output_metadata["source_columns"] == ["a", "b"]


@pytest.mark.asyncio
async def test_data_edit_binding_wins_over_migrated_config(handler, task_factory):
    """Fresh FE edits arrive on the binding side (data_edit_tabs container)
    and must override 042-migrated config values."""
    node = WorkflowNode(
        id="de-mix",
        name="Edit Data",
        node_type=NodeType.DATA_EDIT,
        config={"instruction": "old migrated", "readonly": True},
        input_binding={
            "data_edit_tabs": {
                "main": {
                    "file_id": "{{$input.file_id}}",
                    "instruction": "fresh FE edit",
                },
                "settings_tab": {"readonly": False},
            },
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-mix",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(file_id="f-mix", row_count=1, columns=["a"]),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["file_id"] == "f-mix"
    assert task.output_metadata["instruction"] == "fresh FE edit"


@pytest.mark.asyncio
async def test_data_edit_emits_input_requested_event(handler, task_factory):
    """Handler should emit an InputRequested event with input_schema."""
    node = WorkflowNode(
        id="de-2",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$input.file_id}}",
            "instruction": "Please verify",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-2",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(file_id="f-up-002"),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.node_id == "de-2"
    assert event.run_id == "run-2"
    assert event.instruction == "Please verify"
    assert event.source_file_id == "f-up-002"
    assert event.source_node_id == "input"


@pytest.mark.asyncio
async def test_data_edit_event_carries_root_run_id_and_depth(handler, task_factory):
    """InputRequested must carry root_run_id so realtime routing reaches the root run room."""
    node = WorkflowNode(
        id="de-root",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$input.file_id}}", "instruction": "Edit"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-child",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        parent_run_id="run-parent",
        root_run_id="run-root",
        depth_level=2,
        context=RunContext(variables={"input": build_file_ref(file_id="f-1")}),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.root_run_id == "run-root"
    assert event.parent_run_id == "run-parent"
    assert event.depth == 2
    assert event.source == "workflow-control-plane"


@pytest.mark.asyncio
async def test_data_edit_without_file_id_still_creates_pending(handler, task_factory):
    """Handler should work even when no file_id is in the resolved input (e.g., user will upload later)."""
    node = WorkflowNode(
        id="de-3",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={},
        config={},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-3",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.resolved_input.get("data_mode") == "csv_single"
    # No source when no input_binding
    assert task.output_metadata["source_node_id"] == ""
    assert task.output_metadata["source_node_name"] == ""


@pytest.mark.asyncio
async def test_data_edit_with_custom_data_mode(handler, task_factory):
    """Handler should use data_mode from input_binding when not in resolved input."""
    node = WorkflowNode(
        id="de-4",
        name="Edit Multi",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$input.file_id}}",
            "data_mode": "csv_multi",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-4",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(file_id="f-multi-001"),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["data_mode"] == "csv_multi"


@pytest.mark.asyncio
async def test_data_edit_resolves_scalar_input_binding(handler, task_factory):
    """Handler should resolve non-file scalar values from input_binding too."""
    node = WorkflowNode(
        id="de-5",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$prev.file_id}}",
            "extra_info": "{{$prev.label}}",
        },
        config={},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-5",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "prev": {"file_id": "f-scalar-001", "label": "Monthly Report"},
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["file_id"] == "f-scalar-001"
    assert task.resolved_input["extra_info"] == "Monthly Report"


@pytest.mark.asyncio
async def test_data_edit_tracks_source_node_by_id(handler, task_factory):
    """Handler should identify the source node by ID and include its name."""
    upstream = WorkflowNode(
        id="fetch-1",
        name="Fetch Orders",
        node_type=NodeType.TASK,
        worker_type="http-request-worker",
    )
    data_edit = WorkflowNode(
        id="de-6",
        name="Review Orders",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$fetch-1.file_id}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W",
        nodes=[upstream, data_edit],
        edges=[WorkflowEdge(source_node_id="fetch-1", target_node_id="de-6")],
    )
    run = WorkflowRun(
        id="run-6",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "fetch-1": build_file_ref(
                    file_id="f-orders-001",
                    row_count=42,
                    columns=["order_id", "customer", "amount"],
                ),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(data_edit, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["source_node_id"] == "fetch-1"
    assert task.output_metadata["source_node_name"] == "Fetch Orders"
    assert task.output_metadata["source_columns"] == ["order_id", "customer", "amount"]
    assert task.output_metadata["source_row_count"] == 42

    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.node_id == "de-6"
    assert event.source_file_id == "f-orders-001"
    assert event.source_node_id == "fetch-1"
    assert event.source_node_name == "Fetch Orders"


@pytest.mark.asyncio
async def test_data_edit_tracks_source_node_by_name(handler, task_factory):
    """Handler should identify the source node when referenced by a single-word name."""
    upstream = WorkflowNode(
        id="node-abc",
        name="LoadData",
        node_type=NodeType.TASK,
        worker_type="csv-worker",
    )
    data_edit = WorkflowNode(
        id="de-7",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$LoadData.file_id}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W",
        nodes=[upstream, data_edit],
        edges=[WorkflowEdge(source_node_id="node-abc", target_node_id="de-7")],
    )
    run = WorkflowRun(
        id="run-7",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "LoadData": build_file_ref(
                    file_id="f-load-001",
                    row_count=10,
                    columns=["col_a", "col_b"],
                ),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(data_edit, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["source_node_id"] == "node-abc"
    assert task.output_metadata["source_node_name"] == "LoadData"
    assert task.output_metadata["source_columns"] == ["col_a", "col_b"]
    assert task.output_metadata["source_row_count"] == 10


def _make_upstream_with_artifact() -> dict:
    upstream = build_file_ref(
        file_id="main-001", row_count=1, columns=["name"],
    )
    art = add_artifact(
        upstream,
        file_id="art-001",
        name="uploaded_data",
        artifact_type="array_field",
    )
    art["row_count"] = 50
    art["columns"] = ["col_a", "col_b"]
    return upstream


@pytest.mark.asyncio
async def test_data_edit_resolves_artifacts_namespace_to_owning_node(handler, task_factory):
    """When the binding uses ``{{$artifacts.<name>}}`` and ``register_artifacts``
    has populated the global ``$artifacts`` namespace, the handler must:
      1. resolve file_id to the artifact's CSV file (not the parent),
      2. trace back to the producing node so the frontend shows source info.
    """
    upstream = WorkflowNode(
        id="fetch-1",
        name="Fetch Orders",
        node_type=NodeType.TASK,
        worker_type="csv-worker",
    )
    data_edit = WorkflowNode(
        id="de-art-1",
        name="Edit Uploaded",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$artifacts.uploaded_data}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W",
        nodes=[upstream, data_edit],
        edges=[WorkflowEdge(source_node_id="fetch-1", target_node_id="de-art-1")],
    )
    upstream_ref = _make_upstream_with_artifact()
    variables = {"fetch-1": upstream_ref, "artifacts": {}}
    register_artifacts(variables, upstream_ref)
    run = WorkflowRun(
        id="run-art-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(
        data_edit, run, workflow, topology, None, task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["file_id"] == "art-001"
    assert task.output_metadata["file_id"] == "art-001"
    # Source tracking points at the producing node, not the "artifacts" namespace
    assert task.output_metadata["source_node_id"] == "fetch-1"
    assert task.output_metadata["source_node_name"] == "Fetch Orders"
    assert task.output_metadata["source_columns"] == ["col_a", "col_b"]
    assert task.output_metadata["source_row_count"] == 50


@pytest.mark.asyncio
async def test_data_edit_recovers_when_artifacts_namespace_empty(handler, task_factory):
    """If ``register_artifacts`` never ran (e.g., resumed run, registration
    skipped), the resolver throws because ``$artifacts`` is empty. The handler
    must recover by scanning each node's file_ref for the named artifact and
    still produce a valid PENDING task with proper source metadata.
    """
    upstream = WorkflowNode(
        id="fetch-1",
        name="Fetch Orders",
        node_type=NodeType.TASK,
        worker_type="csv-worker",
    )
    data_edit = WorkflowNode(
        id="de-art-2",
        name="Edit Uploaded",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$artifacts.uploaded_data}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W",
        nodes=[upstream, data_edit],
        edges=[WorkflowEdge(source_node_id="fetch-1", target_node_id="de-art-2")],
    )
    upstream_ref = _make_upstream_with_artifact()
    # Note: artifacts namespace is empty — register_artifacts NOT called.
    variables = {"fetch-1": upstream_ref, "artifacts": {}}
    run = WorkflowRun(
        id="run-art-2",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(
        data_edit, run, workflow, topology, None, task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.resolved_input["file_id"] == "art-001"
    assert task.output_metadata["source_node_id"] == "fetch-1"
    assert task.output_metadata["source_columns"] == ["col_a", "col_b"]
    assert task.output_metadata["source_row_count"] == 50


@pytest.mark.asyncio
async def test_data_edit_unknown_artifact_name_still_raises(handler, task_factory):
    """A binding referencing an artifact that does not exist anywhere in the
    run context should still surface an ExpressionError — recovery is for
    timing/registration gaps, not for typos."""
    from app.layer1_domain.exceptions import ExpressionError

    upstream = WorkflowNode(
        id="fetch-1", name="Fetch", node_type=NodeType.TASK, worker_type="w",
    )
    data_edit = WorkflowNode(
        id="de-art-3",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$artifacts.does_not_exist}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W", nodes=[upstream, data_edit],
        edges=[WorkflowEdge(source_node_id="fetch-1", target_node_id="de-art-3")],
    )
    upstream_ref = _make_upstream_with_artifact()
    variables = {"fetch-1": upstream_ref, "artifacts": {}}
    run = WorkflowRun(
        id="run-art-3", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(variables=variables),
    )
    topology = _make_topology(workflow)

    with pytest.raises(ExpressionError):
        await handler.handle(
            data_edit, run, workflow, topology, None, task_factory,
        )


@pytest.mark.asyncio
async def test_data_edit_node_type_in_pending_set():
    """DATA_EDIT should be in PENDING_NODE_TYPES."""
    from app.layer2_application.orchestration.successor_processor import PENDING_NODE_TYPES
    assert NodeType.DATA_EDIT in PENDING_NODE_TYPES


# ---------------------------------------------------------------------------
# Working file_ref provisioning (file_client wired)
#
# When a real ``file_service_client`` is wired into the handler, dispatch
# must seed a working ``__file_ref`` tree under ``state_updates[node.id]``
# so PATCH /data has a real ``__root__`` + nested ``table_data`` artifact
# to target. ``task.resolved_input`` is the same file_ref tree, so the
# ETag pipeline computes a stable ETag from the same shape PATCH will
# work against.
# ---------------------------------------------------------------------------


class _StubFileClient:
    """Minimal file client stub — handler only needs the object identity to
    enable provisioning. The handler's ``_build_working_ref`` does not call
    out to the file service (no network) so an opaque sentinel is enough."""

    async def upload_file(self, *args, **kwargs):  # pragma: no cover — unused here
        return {"file_id": "stub-uploaded"}


@pytest.fixture
def handler_with_files(resolver):
    return DataEditNodeHandler(resolver, file_service_client=_StubFileClient())


def _is_working_ref(value) -> bool:
    return isinstance(value, dict) and value.get("__file_ref") is True


@pytest.mark.asyncio
async def test_data_edit_provisions_working_ref_seeded_from_upstream(
    handler_with_files, task_factory,
):
    """Handler with file_client wired must produce a file_ref tree where
    ``table_data.file_id`` is the upstream file_id (so first PATCH downloads
    the upstream content, edits it, and uploads a new file_id)."""
    node = WorkflowNode(
        id="de-w1",
        name="Edit Data",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-w1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "src": build_file_ref(
                    file_id="f-up",
                    row_count=2,
                    columns=["sku", "qty"],
                ),
            }
        ),
    )
    topology = _make_topology(workflow)

    result = await handler_with_files.handle(
        node, run, workflow, topology, None, task_factory,
    )

    working = result.state_updates.get("de-w1")
    assert _is_working_ref(working), "state_updates must carry a file_ref tree"
    assert working.get("artifact_type") == "object_field"
    # Root has no scalars but is still minted as a header-only CSV so its
    # file_id is non-empty — uniform with INPUT/HUMAN_ACTION/FILE_UPLOAD.
    assert working.get("file_id") == "stub-uploaded"
    assert working.get("columns") == ["__row_id"]

    artifacts = working.get("artifacts") or []
    assert len(artifacts) == 1, "exactly one nested artifact at provision time"
    table = artifacts[0]
    assert table["name"] == "table_data"
    assert table["artifact_id"] == "table_data"
    assert table["artifact_type"] == "array_field"
    assert table["file_id"] == "f-up", "table_data seeded from upstream file_id"
    assert table["columns"] == ["sku", "qty"]

    # task.resolved_input is the same object pushed via state_updates so the
    # ETag pipeline (GET /data → PATCH /data) operates on a single tree.
    task = result.tasks_to_dispatch[0]
    assert task.resolved_input is working


@pytest.mark.asyncio
async def test_data_edit_provisions_working_ref_with_artifact_lookup(
    handler_with_files, task_factory,
):
    """When the upstream is a multi-artifact tree and ``$artifacts.<name>``
    selects one of them, the seeded ``table_data.file_id`` must point at the
    selected artifact, not the parent."""
    upstream_node = WorkflowNode(
        id="fetch-1", name="Fetch", node_type=NodeType.TASK, worker_type="w",
    )
    node = WorkflowNode(
        id="de-w2",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$artifacts.uploaded_data}}"},
    )
    workflow = Workflow(
        id="wf-1", name="W",
        nodes=[upstream_node, node],
        edges=[WorkflowEdge(source_node_id="fetch-1", target_node_id="de-w2")],
    )
    upstream_ref = _make_upstream_with_artifact()
    variables = {"fetch-1": upstream_ref, "artifacts": {}}
    register_artifacts(variables, upstream_ref)
    run = WorkflowRun(
        id="run-w2",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables),
    )
    topology = _make_topology(workflow)

    result = await handler_with_files.handle(
        node, run, workflow, topology, None, task_factory,
    )

    working = result.state_updates.get("de-w2")
    assert _is_working_ref(working)
    table = working["artifacts"][0]
    assert table["file_id"] == "art-001", (
        "table_data must seed from the named artifact, not its parent"
    )
    assert table["columns"] == ["col_a", "col_b"]


@pytest.mark.asyncio
async def test_data_edit_provisions_empty_table_data_when_no_upstream(
    handler_with_files, task_factory,
):
    """When the input_binding is empty, the handler still seeds a working
    ref so PATCH /data can author content from scratch. Critically, the
    nested table_data artifact must carry a non-empty ``file_id`` —
    EditNodeDataUseCase forwards array-field operations to the
    row-transform factory, which rejects empty ``file_id`` with
    INVALID_INPUT. The handler mints a header-only CSV via
    ``file_service_client.upload_file`` to satisfy that contract."""
    node = WorkflowNode(
        id="de-w3",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={},  # no upstream
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-w3", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    )
    topology = _make_topology(workflow)

    result = await handler_with_files.handle(
        node, run, workflow, topology, None, task_factory,
    )

    working = result.state_updates.get("de-w3")
    assert _is_working_ref(working)
    table = working["artifacts"][0]
    assert table["name"] == "table_data"
    # The stub file client returns "stub-uploaded" for upload_file —
    # what matters is that the handler called upload_file at all rather
    # than leaving file_id empty.
    assert table["file_id"] == "stub-uploaded", (
        "no upstream → handler must mint a header-only CSV, not leave "
        "file_id empty (would 400 INVALID_INPUT on first PATCH)"
    )


@pytest.mark.asyncio
async def test_data_edit_path_expression_uses_resolved_file_columns(
    resolver, task_factory,
):
    """A binding that plucks a file_id from INSIDE the source node's
    output (e.g. ``{{$src.response_body.data.someFileId}}``) points at a
    DIFFERENT file than the source node's own output artifact. The
    source ref's tracked columns must NOT be adopted; the working ref's
    columns come from the OData probe of the resolved file instead.

    Regression: workflow 83c9af47 — http_request output file_ref columns
    (flattened response attributes) leaked into the DATA_EDIT working
    ref, so GET /schema showed response-header columns instead of the
    validation-result file's real columns."""

    class _ProbingStub(_StubFileClient):
        async def get_columns_via_odata(self, file_id):
            assert file_id == "f-validation", (
                "probe must target the resolved file, not the source ref"
            )
            return ["Record Index", "Status", "Message"]

    handler = DataEditNodeHandler(
        resolver, file_service_client=_ProbingStub(),
    )
    node = WorkflowNode(
        id="de-w5",
        name="Review",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.response_body.data.validationResultFileId}}",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    src_ref = build_file_ref(
        file_id="f-src-own-output",
        row_count=1,
        columns=["output.uri", "status_code", "response_body.success"],
    )
    src_ref["response_body"] = {
        "data": {"validationResultFileId": "f-validation"},
    }
    run = WorkflowRun(
        id="run-w5", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(variables={"src": src_ref}),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    task = result.tasks_to_dispatch[0]
    working = result.state_updates.get("de-w5")
    assert _is_working_ref(working)
    table = working["artifacts"][0]
    assert table["file_id"] == "f-validation"
    assert table["columns"] == ["Record Index", "Status", "Message"], (
        "columns must come from the resolved file's OData probe, not the "
        "source node's own output artifact"
    )
    assert task.output_metadata["source_columns"] == [
        "Record Index", "Status", "Message",
    ]
    # Source-node identity tracking is unaffected by the column guard.
    assert task.output_metadata["file_id"] == "f-validation"


@pytest.mark.asyncio
async def test_data_edit_whole_output_binding_still_adopts_source_columns(
    handler_with_files, task_factory,
):
    """Counterpart guard-rail: when the binding references the source
    file itself (``{{$src.file_id}}`` resolves to the ref's own
    file_id), the tracked columns are still adopted — no behavior
    change for the direct-upstream case."""
    node = WorkflowNode(
        id="de-w6",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-w6", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(variables={
            "src": build_file_ref(
                file_id="f-up", row_count=3, columns=["sku", "qty"],
            ),
        }),
    )
    topology = _make_topology(workflow)

    result = await handler_with_files.handle(
        node, run, workflow, topology, None, task_factory,
    )

    task = result.tasks_to_dispatch[0]
    assert task.output_metadata["source_columns"] == ["sku", "qty"]
    assert task.output_metadata["source_row_count"] == 3
    working = result.state_updates.get("de-w6")
    assert working["artifacts"][0]["columns"] == ["sku", "qty"]


@pytest.mark.asyncio
async def test_data_edit_skips_provisioning_without_file_client(
    handler, task_factory,
):
    """Without a wired file_client (test/mock setups), the legacy
    plain-dict ``resolved_input`` path must keep working — guards the
    existing ten dispatch tests above."""
    node = WorkflowNode(
        id="de-w4",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = WorkflowRun(
        id="run-w4", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(variables={
            "src": build_file_ref(file_id="f-up", row_count=0, columns=[]),
        }),
    )
    topology = _make_topology(workflow)

    result = await handler.handle(node, run, workflow, topology, None, task_factory)

    assert "de-w4" not in result.state_updates, (
        "no provisioning when file_client is None"
    )
    task = result.tasks_to_dispatch[0]
    assert isinstance(task.resolved_input, dict)
    assert not _is_working_ref(task.resolved_input), (
        "resolved_input stays as the legacy plain dict"
    )
    assert task.resolved_input["file_id"] == "f-up"


# ─────────────────── _parse_array_of_objects (coercion) ───────────────────


def test_parse_array_of_objects_coerces_json_string():
    from app.layer2_application.node_handlers.data_edit_handler import (
        _parse_array_of_objects,
    )
    assert _parse_array_of_objects('[{"a":1},{"b":2}]') == [{"a": 1}, {"b": 2}]


def test_parse_array_of_objects_wraps_single_dict():
    from app.layer2_application.node_handlers.data_edit_handler import (
        _parse_array_of_objects,
    )
    assert _parse_array_of_objects({"x": 1}) == [{"x": 1}]


def test_parse_array_of_objects_drops_non_dict_items():
    from app.layer2_application.node_handlers.data_edit_handler import (
        _parse_array_of_objects,
    )
    assert _parse_array_of_objects([{"a": 1}, 5, "x", {"b": 2}]) == [
        {"a": 1}, {"b": 2},
    ]


def test_parse_array_of_objects_empty_for_garbage():
    from app.layer2_application.node_handlers.data_edit_handler import (
        _parse_array_of_objects,
    )
    assert _parse_array_of_objects("not json") == []
    assert _parse_array_of_objects("   ") == []
    assert _parse_array_of_objects(None) == []
    assert _parse_array_of_objects(42) == []


# ─────────────────── actions / settings runtime injection ───────────────────


def _data_edit_run_with_columns():
    """Build a run whose ``src`` upstream has tracked columns so the handler
    can synthesize a real schema (the file-driven path is bypassed because
    no file_client is wired)."""
    return WorkflowRun(
        id="run-as",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables={
            "src": build_file_ref(file_id="f-up", columns=["sku", "qty"]),
        }),
    )


@pytest.mark.asyncio
async def test_actions_json_string_is_parsed_and_injected(handler, task_factory):
    """``actions`` from the palette textarea (JSON string) ends up at the
    table's ``wrapperProps.actions`` and on output_metadata."""
    node = WorkflowNode(
        id="de-as1",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "actions": '[{"id":"approve","label":"Approve"}]',
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["actions"] == [{"id": "approve", "label": "Approve"}]
    table = md["input_schema"][0]
    assert table["fieldConfig"]["wrapperProps"]["actions"] == [
        {"id": "approve", "label": "Approve"}
    ]


@pytest.mark.asyncio
async def test_actions_dict_is_wrapped_to_list(handler, task_factory):
    """A programmatic dict (not a JSON string) is coerced to ``[dict]``."""
    node = WorkflowNode(
        id="de-as2",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "actions": {"id": "single"},
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["actions"] == [{"id": "single"}]
    table = md["input_schema"][0]
    assert table["fieldConfig"]["wrapperProps"]["actions"] == [{"id": "single"}]


@pytest.mark.asyncio
async def test_actions_garbage_yields_no_wrapper_actions(handler, task_factory):
    """Unparseable / missing ``actions`` → empty meta list and no
    ``wrapperProps.actions`` key in the schema (kept clean)."""
    node = WorkflowNode(
        id="de-as3",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "actions": "not json",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["actions"] == []
    table = md["input_schema"][0]
    assert "actions" not in table["fieldConfig"].get("wrapperProps", {})


@pytest.mark.asyncio
async def test_settings_appended_as_siblings_of_row_template(handler, task_factory):
    """Each parsed ``settings`` entry sits next to ``fields[0]`` (the row
    template), not inside it."""
    node = WorkflowNode(
        id="de-as4",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "settings": '[{"key":"meta1"},{"key":"meta2"}]',
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["settings"] == [{"key": "meta1"}, {"key": "meta2"}]
    table = md["input_schema"][0]
    keys = [f.get("key") for f in table["fields"]]
    # row template is keyed "0", then the two settings siblings follow
    assert keys == ["0", "meta1", "meta2"]


@pytest.mark.asyncio
async def test_readonly_actions_settings_compose(handler, task_factory):
    """All three independent toggles coexist on the same table dict."""
    node = WorkflowNode(
        id="de-as5",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "readonly": True,
            "actions": '[{"id":"x"}]',
            "settings": '[{"key":"s1"}]',
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    table = md["input_schema"][0]
    fc = table["fieldConfig"]
    assert fc["wrapperProps"]["actions"] == [{"id": "x"}]
    assert fc["wrapperProps"]["readonly"] is True
    # readonly mode must omit isAdd/isDelete so the FE hides row-mutation buttons
    assert "isAdd" not in fc["wrapperProps"]
    assert "isDelete" not in fc["wrapperProps"]
    keys = [f.get("key") for f in table["fields"]]
    assert keys == ["0", "s1"]
    assert md["readonly"] is True
    assert md["actions"] == [{"id": "x"}]
    assert md["settings"] == [{"key": "s1"}]


@pytest.mark.asyncio
async def test_non_readonly_sets_isadd_isdelete(handler, task_factory):
    """When readonly is false (or omitted), the table-level wrapperProps
    advertises ``isAdd: True`` and ``isDelete: True`` so the FE renders
    row-add and row-delete affordances."""
    node = WorkflowNode(
        id="de-rw",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    table = result.tasks_to_dispatch[0].output_metadata["input_schema"][0]
    wp = table["fieldConfig"]["wrapperProps"]
    assert wp["isAdd"] is True
    assert wp["isDelete"] is True
    assert "readonly" not in wp


@pytest.mark.asyncio
async def test_odata_query_surfaced_in_table_wrapper_props(handler, task_factory):
    """The configured ``query`` (OData filter) must reach the table's
    ``wrapperProps.query`` so the FE seeds its row fetch with the filter
    instead of rendering the full dataset."""
    node = WorkflowNode(
        id="de-q",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "query": "$filter=qty gt 5&$top=10",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    table = md["input_schema"][0]
    assert table["fieldConfig"]["wrapperProps"]["query"] == {
        "$filter": "qty gt 5", "$top": "10",
    }
    # and it is also carried on metadata for the runtime GET /schema path
    assert md["query_params"] == {"$filter": "qty gt 5", "$top": "10"}


@pytest.mark.asyncio
async def test_no_query_omits_wrapper_props_query(handler, task_factory):
    """Without a configured query, ``wrapperProps`` carries no ``query`` key
    (the FE falls back to fetching the full dataset)."""
    node = WorkflowNode(
        id="de-noq",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    table = result.tasks_to_dispatch[0].output_metadata["input_schema"][0]
    assert "query" not in table["fieldConfig"]["wrapperProps"]


# ─────────────────── schema carries no row data ───────────────────


@pytest.mark.asyncio
async def test_table_default_absent_when_no_rows(handler, task_factory):
    """Schema never embeds rows — the FE fetches data directly from the
    file-service via OData. The ``default`` key must not appear on the
    table field, regardless of file_client presence or upstream state."""
    node = WorkflowNode(
        id="de-no-def",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    table = result.tasks_to_dispatch[0].output_metadata["input_schema"][0]
    assert "default" not in table


# ─────────────────── custom_data_update ───────────────────


@pytest.mark.asyncio
async def test_custom_data_update_flips_table_key(handler, task_factory):
    """When ``custom_data_update`` is true, the runtime table is keyed
    ``__table_data`` (instead of ``table_data``) so the FE routes edits
    through the custom-update path."""
    node = WorkflowNode(
        id="de-cdu1",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "file_id": "{{$src.file_id}}",
            "custom_data_update": True,
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["custom_data_update"] is True
    assert md["input_schema"][0]["key"] == "__table_data"


@pytest.mark.asyncio
async def test_custom_data_update_default_keeps_legacy_key(handler, task_factory):
    """Default (False / missing) keeps the original ``table_data`` key."""
    node = WorkflowNode(
        id="de-cdu2",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={"file_id": "{{$src.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["custom_data_update"] is False
    assert md["input_schema"][0]["key"] == "table_data"


# ─────────────────── tab-flatten resolver ───────────────────


def test_flatten_tab_config_lifts_nested_tabs():
    """Lifts ``{tab: {item: {field: v}}}`` two levels to a flat dict."""
    from app.layer2_application.node_handlers.data_edit_handler import (
        _flatten_tab_config,
    )
    nested = {
        "data_edit_tabs": {
            "main": {"file_id": "F", "readonly": True},
            "settings_tab": {"actions": "[]", "settings": None},
        },
    }
    flat = _flatten_tab_config(nested)
    assert flat == {
        "file_id": "F", "readonly": True,
        "actions": "[]", "settings": None,
    }


def test_flatten_tab_config_passes_legacy_flat_through():
    """Old payloads with no nesting are unchanged."""
    from app.layer2_application.node_handlers.data_edit_handler import (
        _flatten_tab_config,
    )
    flat = {"file_id": "X", "readonly": False, "query": "$top=5"}
    assert _flatten_tab_config(flat) == flat


def test_flatten_tab_config_preserves_field_level_dicts():
    """A top-level dict whose values are scalars (not dicts) is a regular
    field, not a tab container — keep it untouched."""
    from app.layer2_application.node_handlers.data_edit_handler import (
        _flatten_tab_config,
    )
    src = {
        "data_edit_tabs": {"main": {"file_id": "F"}},
        "input_types": {"file_id": "string"},
    }
    out = _flatten_tab_config(src)
    assert out["file_id"] == "F"
    assert out["input_types"] == {"file_id": "string"}


@pytest.mark.asyncio
async def test_handler_reads_fe_nested_tab_payload(handler, task_factory):
    """The exact FE-submitted shape (TabWrapper nesting on input_binding)
    reaches the same output_metadata as a legacy flat payload would."""
    node = WorkflowNode(
        id="de-nest",
        name="Edit",
        node_type=NodeType.DATA_EDIT,
        input_binding={
            "data_edit_tabs": {
                "main": {
                    "file_id": "{{$src.file_id}}",
                    "artifact_key": "art",
                    "instruction": "INST",
                    "data_mode": "csv_multi",
                    "query": "$top=5",
                    "readonly": True,
                },
                "settings_tab": {
                    "actions": '[{"id":"approve"}]',
                    "settings": '[{"key":"meta"}]',
                },
            },
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run_with_columns()

    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )

    md = result.tasks_to_dispatch[0].output_metadata
    assert md["instruction"] == "INST"
    assert md["artifact_key"] == "art"
    assert md["query_params"] == {"$top": "5"}
    assert md["readonly"] is True
    assert md["actions"] == [{"id": "approve"}]
    assert md["settings"] == [{"key": "meta"}]
    # data_mode flows through resolved_input
    assert result.tasks_to_dispatch[0].resolved_input["data_mode"] == "csv_multi"


# ─────────────────── palette schema (tabs) ───────────────────


def test_palette_data_edit_uses_tab_wrapper():
    """Palette returns one top-level TabWrapper containing two TabItemWrappers."""
    from app.layer1_domain.node_spec import get_spec
    from app.layer1_domain.node_spec.form_renderer import render_input_schema
    from app.layer1_domain.value_objects.node_type import NodeType as _NT
    de = render_input_schema(get_spec(_NT.DATA_EDIT))
    assert len(de) == 1
    top = de[0]
    assert top["fieldConfig"]["fieldWrapper"] == "TabWrapper"
    tabs = top["fields"]
    assert len(tabs) == 2

    main, settings_tab = tabs
    assert main["fieldConfig"]["fieldWrapper"] == "TabItemWrapper"
    assert main["fieldConfig"]["wrapperProps"]["label"] == "Configuration"
    main_keys = [f["key"] for f in main["fields"]]
    assert main_keys == [
        "file_id", "artifact_key", "instruction",
        "data_mode", "query",
    ]

    assert settings_tab["fieldConfig"]["fieldWrapper"] == "TabItemWrapper"
    assert settings_tab["fieldConfig"]["wrapperProps"]["label"] == "Settings"
    settings_keys = [f["key"] for f in settings_tab["fields"]]
    assert settings_keys == [
        "actions", "settings", "readonly", "custom_data_update",
        "selectionMode",  # SA-1239: designer-facing Select Mode radio
    ]


def test_palette_data_edit_select_mode_is_select():
    """SA-1239: the Select Mode field renders in the Settings tab as a
    SelectControl (dropdown) carrying {id, value} options (none/None,
    single/Single, multiple/Multiple) — same shape as the Data Mode select."""
    from app.layer1_domain.node_spec import get_spec
    from app.layer1_domain.node_spec.form_renderer import render_input_schema
    from app.layer1_domain.value_objects.node_type import NodeType as _NT
    settings_tab = render_input_schema(get_spec(_NT.DATA_EDIT))[0]["fields"][1]
    field = next(f for f in settings_tab["fields"] if f["key"] == "selectionMode")
    fc = field["fieldConfig"]
    assert field["outputType"] == "string"
    assert fc["fieldControl"] == "SelectControl"
    assert fc["wrapperProps"]["label"] == "Select Mode"
    assert fc["controlProps"]["options"] == [
        {"id": "none", "value": "None"},
        {"id": "single", "value": "Single"},
        {"id": "multiple", "value": "Multiple"},
    ]


# --- SA-1239: selectionMode reaches the DISPATCH-time schema -----------------
# The approval "Input Requested" JSON the FE first receives is
# ``task.output_metadata['input_schema']``, built by
# ``_build_dynamic_table_schema``. Before the fix that builder never emitted
# selectionMode, so the configured mode was dropped at dispatch and only
# reappeared on a later runtime GET /schema. These pin the two paths together.


def _dispatch_table_wrapper_props(task) -> dict:
    """The OData table's wrapperProps from a dispatched DATA_EDIT input_schema."""
    schema = task.output_metadata["input_schema"]
    assert schema, "dispatch must produce a non-empty input_schema"
    return schema[0]["fieldConfig"]["wrapperProps"]


def _data_edit_run(node: WorkflowNode, columns: list[str]) -> WorkflowRun:
    return WorkflowRun(
        id="run-sel",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={
                "input": build_file_ref(
                    file_id="f-sel", row_count=2, columns=columns,
                ),
            }
        ),
    )


@pytest.mark.asyncio
async def test_data_edit_dispatch_emits_selection_mode_from_config(
    handler, task_factory,
):
    """Issue #2: a configured selectionMode must appear in the dispatch schema's
    wrapperProps (previously absent — the FE fell back to multi-select)."""
    node = WorkflowNode(
        id="de-sel", name="Edit", node_type=NodeType.DATA_EDIT,
        config={"file_id": "{{$input.file_id}}", "selectionMode": "multiple"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run(node, ["a", "b"])
    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )
    task = result.tasks_to_dispatch[0]
    assert _dispatch_table_wrapper_props(task).get("selectionMode") == "multiple"
    assert task.output_metadata["selectionMode"] == "multiple"


@pytest.mark.asyncio
async def test_data_edit_dispatch_readonly_defaults_selection_single(
    handler, task_factory,
):
    """AC3: a readonly approval table with no configured mode defaults to a
    single-row radio at dispatch time (not just at runtime)."""
    node = WorkflowNode(
        id="de-ro", name="Edit", node_type=NodeType.DATA_EDIT,
        config={"file_id": "{{$input.file_id}}", "readonly": True},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run(node, ["a", "b"])
    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )
    task = result.tasks_to_dispatch[0]
    assert _dispatch_table_wrapper_props(task).get("selectionMode") == "single"


@pytest.mark.asyncio
async def test_data_edit_dispatch_none_omits_selection_mode(
    handler, task_factory,
):
    """Issue #1: choosing "none" — even on a readonly table — suppresses the
    prop; the dispatch wrapperProps carries no selectionMode."""
    node = WorkflowNode(
        id="de-none", name="Edit", node_type=NodeType.DATA_EDIT,
        config={
            "file_id": "{{$input.file_id}}",
            "readonly": True,
            "selectionMode": "none",
        },
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run(node, ["a", "b"])
    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )
    task = result.tasks_to_dispatch[0]
    assert "selectionMode" not in _dispatch_table_wrapper_props(task)
    assert task.output_metadata["selectionMode"] == "none"


@pytest.mark.asyncio
async def test_data_edit_dispatch_editable_omits_selection_mode(
    handler, task_factory,
):
    """Majority case / regression guard: an editable table with no configured
    mode emits no selectionMode at dispatch (historic FE multi-select default)."""
    node = WorkflowNode(
        id="de-ed", name="Edit", node_type=NodeType.DATA_EDIT,
        config={"file_id": "{{$input.file_id}}"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run(node, ["a", "b"])
    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )
    task = result.tasks_to_dispatch[0]
    assert "selectionMode" not in _dispatch_table_wrapper_props(task)
    assert task.output_metadata["selectionMode"] == ""


@pytest.mark.asyncio
async def test_data_edit_dispatch_emits_explicit_single(handler, task_factory):
    """Completes the truth table: an explicitly configured single mode is
    emitted verbatim at dispatch (not only the readonly-derived default)."""
    node = WorkflowNode(
        id="de-s", name="Edit", node_type=NodeType.DATA_EDIT,
        config={"file_id": "{{$input.file_id}}", "selectionMode": "single"},
    )
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])
    run = _data_edit_run(node, ["a", "b"])
    result = await handler.handle(
        node, run, workflow, _make_topology(workflow), None, task_factory,
    )
    task = result.tasks_to_dispatch[0]
    assert _dispatch_table_wrapper_props(task).get("selectionMode") == "single"
    assert task.output_metadata["selectionMode"] == "single"
