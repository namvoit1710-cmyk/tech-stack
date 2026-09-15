"""Unit tests for GetNodeSchemaUseCase."""

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.schema_providers import (
    DataEditSchemaProvider,
    StaticSchemaProvider,
)
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.get_node_schema.use_cases.get_node_schema_use_case import (
    GetNodeSchemaCommand,
    GetNodeSchemaUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger


class _StubPendingHandler:
    """Minimal handler stub that just exposes the right schema provider.

    The use case never calls ``handle()`` — it only needs
    ``get_schema_provider()`` to know which provider drives /schema for
    this node type. A real :class:`PendingHandlerBase` subclass would
    work too, but we'd have to construct a VariableResolver — overkill
    for unit tests of the schema endpoint.
    """
    def __init__(self, provider) -> None:
        self._provider = provider

    def get_schema_provider(self):
        return self._provider


def _build_handler_registry() -> NodeHandlerRegistry:
    registry = NodeHandlerRegistry()
    registry.register(NodeType.HUMAN_ACTION, _StubPendingHandler(StaticSchemaProvider()))
    registry.register(NodeType.INPUT, _StubPendingHandler(StaticSchemaProvider()))
    registry.register(NodeType.FILE_UPLOAD, _StubPendingHandler(StaticSchemaProvider()))
    registry.register(NodeType.DATA_EDIT, _StubPendingHandler(DataEditSchemaProvider()))
    return registry


def _build_uc(view_store=None):
    logger = DummyLogger()
    return (
        GetNodeSchemaUseCase(
            logger=logger,
            run_repo=MemoryRunRepository(),
            task_repo=MemoryTaskRepository(),
            workflow_repo=MemoryWorkflowRepository(),
            handler_registry=_build_handler_registry(),
            run_context_view_store=view_store,
        ),
        logger,
    )


class _StubView:
    def __init__(self, variables):
        self.variables = dict(variables)


class _StubViewStore:
    """Minimal IRunContextViewStore double for the DATA_EDIT dynamic-schema path.

    Only ``.get(run_id)`` is exercised; returning a tiny object whose
    ``.variables`` dict carries a working file_ref is enough.
    """
    def __init__(self):
        self._views: dict[str, _StubView] = {}

    def put(self, run_id: str, variables: dict):
        self._views[run_id] = _StubView(variables)

    def get(self, run_id: str):
        return self._views.get(run_id)


def _file_ref_with_columns(columns):
    return {
        "__file_ref": True,
        "file_id": "",
        "artifact_id": "__root__",
        "artifacts": [{
            "artifact_id": "table_data",
            "name": "table_data",
            "file_id": "f-table",
            "columns": list(columns),
            "artifact_type": "array_field",
        }],
    }


def _inject(uc, run=None, workflow=None, tasks=()):
    if workflow is not None:
        uc.workflow_repo.save(workflow)
    if run is not None:
        uc.run_repo.save(run)
    for t in tasks:
        uc.task_repo.save(t)


def _mk_workflow(node_config=None, node_type=NodeType.HUMAN_ACTION,
                 definition_snapshot=None):
    node = WorkflowNode(
        id="n1", name="Review",
        node_type=node_type,
        config=node_config or {},
        definition_snapshot=definition_snapshot,
    )
    return Workflow(id="wf-1", name="Test", nodes=[node], edges=[])


def _mk_run():
    return WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    )


def _mk_task(node_id="n1", task_id="t-1", created_at="2026-01-01T00:00:00Z",
             output_metadata=None, status=TaskStatus.PENDING):
    t = Task(
        id=task_id, run_id="r-1", node_id=node_id,
        worker_type="", status=status,
        created_at=created_at,
    )
    t.output_metadata = output_metadata or {}
    return t


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_design_time_schema_from_node_definition_template():
    uc, _ = _build_uc()
    wf = _mk_workflow(definition_snapshot={
        "input_schema": {"fields": [{"key": "a"}]},
    })
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[_mk_task()])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.error is None
    assert result.schema == {"fields": [{"key": "a"}]}
    assert result.schema_source == "design_time"
    assert result.node_kind == "HUMAN_ACTION"
    assert result.node_state == "PENDING"
    assert result.schema_version.startswith("sha256:")
    assert result.node == {
        "name": "Review",
        "node_type": "HUMAN_ACTION",
        "description": "",
        "worker_type": "",
    }


@pytest.mark.asyncio
async def test_node_info_carries_design_time_metadata():
    """``node`` mirrors the `_node` event-envelope contract: name,
    node_type, description, worker_type."""
    uc, _ = _build_uc()
    node = WorkflowNode(
        id="n1", name="Fetch invoices",
        node_type=NodeType.HUMAN_ACTION,
        description="Reviewer approves the invoice batch",
        worker_type="http-request-worker",
        config={},
    )
    wf = Workflow(id="wf-1", name="Test", nodes=[node], edges=[])
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[_mk_task()])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.error is None
    assert result.node == {
        "name": "Fetch invoices",
        "node_type": "HUMAN_ACTION",
        "description": "Reviewer approves the invoice batch",
        "worker_type": "http-request-worker",
    }


@pytest.mark.asyncio
async def test_node_info_present_on_schema_source_none_path():
    """Node types without a schema provider still return the node block."""
    uc, _ = _build_uc()
    wf = _mk_workflow(node_type=NodeType.TASK)  # TASK not in the registry
    _inject(uc, run=_mk_run(), workflow=wf)

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.schema_source == "none"
    assert result.node["name"] == "Review"
    assert result.node["node_type"] == "TASK"


@pytest.mark.asyncio
async def test_returns_runtime_schema_from_task_output_metadata():
    uc, _ = _build_uc()
    wf = _mk_workflow(
        node_type=NodeType.DATA_EDIT,
        definition_snapshot={"input_schema": {"fields": [{"key": "design_time"}]}},
    )
    task = _mk_task(output_metadata={"input_schema": {"fields": [{"key": "runtime"}]}})
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[task])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.schema == {"fields": [{"key": "runtime"}]}
    assert result.schema_source == "runtime"
    assert result.node_kind == "DATA_EDIT"


@pytest.mark.asyncio
async def test_returns_empty_defaults_when_no_schema_anywhere():
    uc, _ = _build_uc()
    wf = _mk_workflow(node_config={})
    _inject(uc, run=_mk_run(), workflow=wf)  # no task either

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.error is None
    assert result.schema == []
    assert result.schema_version == "v0"
    assert result.schema_source == "design_time"
    assert result.node_state == "PENDING"


@pytest.mark.asyncio
async def test_picks_latest_task_when_multiple_exist():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    older = _mk_task(task_id="t-old", created_at="2026-01-01T00:00:00Z",
                     output_metadata={"input_schema": {"fields": [{"key": "old"}]}})
    newer = _mk_task(task_id="t-new", created_at="2026-02-01T00:00:00Z",
                     output_metadata={"input_schema": {"fields": [{"key": "new"}]}})
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[older, newer])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))
    assert result.schema == {"fields": [{"key": "new"}]}


@pytest.mark.asyncio
async def test_task_id_param_targets_specific_task():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    t1 = _mk_task(task_id="t-1", created_at="2026-01-01T00:00:00Z",
                  output_metadata={"input_schema": {"fields": [{"key": "old"}]}})
    t2 = _mk_task(task_id="t-2", created_at="2026-02-01T00:00:00Z",
                  output_metadata={"input_schema": {"fields": [{"key": "new"}]}})
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[t1, t2])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1", task_id="t-1"))
    assert result.schema == {"fields": [{"key": "old"}]}


@pytest.mark.asyncio
async def test_task_id_ignored_when_it_belongs_to_another_node():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    # Task with task_id='t-2' is for a DIFFERENT node — should be ignored.
    t_other = _mk_task(node_id="n99", task_id="t-2",
                       output_metadata={"input_schema": {"fields": [{"key": "wrong"}]}})
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[t_other])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1", task_id="t-2"))
    # No matching task for (n1, t-2) — falls back to design-time / defaults.
    assert result.schema_source == "design_time"
    assert result.schema == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_not_found():
    uc, _ = _build_uc()
    result = await uc.execute(GetNodeSchemaCommand(run_id="missing", node_id="n1"))
    assert result.error_code == "RUN_NOT_FOUND"
    assert result.error_details == {"run_id": "missing"}


@pytest.mark.asyncio
async def test_workflow_not_found():
    uc, _ = _build_uc()
    # Save run pointing at a missing workflow.
    run = WorkflowRun(id="r-1", workflow_id="missing-wf",
                     status=RunStatus.RUNNING, context=RunContext(variables={}))
    _inject(uc, run=run)
    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))
    assert result.error_code == "WORKFLOW_NOT_FOUND"


@pytest.mark.asyncio
async def test_node_not_found():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    _inject(uc, run=_mk_run(), workflow=wf)
    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="nope"))
    assert result.error_code == "NODE_NOT_FOUND"


# ---------------------------------------------------------------------------
# node_state semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_state_completed_when_in_completed_set():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    run = _mk_run()
    run.context.completed_nodes.add("n1")
    _inject(uc, run=run, workflow=wf)
    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))
    assert result.node_state == "COMPLETED"


@pytest.mark.asyncio
async def test_node_state_skipped_wins_over_task_status():
    uc, _ = _build_uc()
    wf = _mk_workflow()
    run = _mk_run()
    run.context.skipped_nodes.add("n1")
    _inject(uc, run=run, workflow=wf, tasks=[_mk_task(status=TaskStatus.RUNNING)])
    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))
    assert result.node_state == "SKIPPED"


# ---------------------------------------------------------------------------
# DATA_EDIT dynamic schema (synthesized from view-store columns)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_data_edit_synthesizes_from_view_columns():
    """When the view exposes a working file_ref, GET /schema must reflect
    its current ``columns`` (post-PATCH state) rather than the cached
    dispatch-time schema on task.output_metadata."""
    view_store = _StubViewStore()
    uc, _ = _build_uc(view_store=view_store)
    wf = _mk_workflow(node_type=NodeType.DATA_EDIT)
    # Task carries a stale cached schema — the dynamic path must override.
    task = _mk_task(output_metadata={
        "input_schema": [{"key": "stale_table", "columns": [{"key": "old_col"}]}],
    })
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[task])
    view_store.put("r-1", {"n1": _file_ref_with_columns(["sku", "qty"])})

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))

    assert result.error is None
    assert result.schema_source == "runtime"
    assert isinstance(result.schema, list) and len(result.schema) == 1
    # The auto-form shape nests columns two levels deep — table → row → cells.
    cells = result.schema[0]["fields"][0]["fields"]
    keys = [c.get("key") for c in cells]
    assert keys == ["sku", "qty"]


@pytest.mark.asyncio
async def test_data_edit_schema_version_changes_when_columns_change():
    view_store = _StubViewStore()
    uc, _ = _build_uc(view_store=view_store)
    wf = _mk_workflow(node_type=NodeType.DATA_EDIT)
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[_mk_task()])

    view_store.put("r-1", {"n1": _file_ref_with_columns(["sku", "qty"])})
    v1 = (await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))).schema_version

    view_store.put("r-1", {"n1": _file_ref_with_columns(["sku", "qty", "region"])})
    v2 = (await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))).schema_version

    assert v1 != v2
    assert v1.startswith("sha256:") and v2.startswith("sha256:")


@pytest.mark.asyncio
async def test_data_edit_falls_back_to_cached_schema_when_view_empty():
    """Before dispatch finishes (or in tests with no view store wired) the
    use case must still return the dispatch-time cached schema instead of
    erroring."""
    uc, _ = _build_uc()  # no view store
    wf = _mk_workflow(node_type=NodeType.DATA_EDIT)
    cached = [{"key": "table_data", "columns": [{"key": "sku"}]}]
    task = _mk_task(output_metadata={"input_schema": cached})
    _inject(uc, run=_mk_run(), workflow=wf, tasks=[task])

    result = await uc.execute(GetNodeSchemaCommand(run_id="r-1", node_id="n1"))
    assert result.schema == cached
    assert result.schema_source == "runtime"
