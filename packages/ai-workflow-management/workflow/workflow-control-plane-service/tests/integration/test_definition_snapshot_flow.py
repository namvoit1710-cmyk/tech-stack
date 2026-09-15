"""Business-node `definition_snapshot` end-to-end (integration Module 5).

NodeSpec Phase 4 moved the node-definition resolution artifact off
``config["_node_definition"]``/``["_node_internals"]`` onto the
dedicated ``WorkflowNode.definition_snapshot`` field. These tests walk
the REAL flow: seed a NodeDefinition → save a workflow referencing it
over HTTP (enrichment writes the snapshot) → dispatch (locked_defaults
+ input_mapping) → completion (output_mapping) → read surfaces
(GET /schema, GET node-execution) → re-save refresh.
"""

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner

_DEFN_INPUT_SCHEMA = [
    {"key": "note", "outputType": "string",
     "fieldConfig": {"wrapperProps": {"label": "Note"}}},
]


def _definition(**overrides) -> NodeDefinition:
    base = dict(
        id="nd-1", tenant_id="t1", name="Get Orders",
        base_type=NodeType.TASK, base_worker_id="http-worker",
        locked_defaults={"method": "GET"},
        input_mapping={"data_ref": "{{$custom.note}}"},
        output_mapping={"summary": "{{$_output.status_code}}"},
        input_schema=_DEFN_INPUT_SCHEMA,
        output_schema=[{"key": "summary", "outputType": "number",
                        "fieldConfig": {"wrapperProps": {"label": "Summary"}}}],
        data_mode="csv_single",
        edit_mode="before_dispatch",
    )
    base.update(overrides)
    return NodeDefinition(**base)


@pytest.fixture
def container():
    c = build_app_container()
    repo = c["_infra"].repos.node_definition
    repo.save(_definition())
    # JSON-mode variant: csv data modes rebuild resolved_input around the
    # uploaded file (engine `_apply_csv_data_mode`), which would hide the
    # locked-default/input-mapping assertions in the dispatch tests.
    repo.save(_definition(id="nd-json", data_mode="json", edit_mode="none"))
    # PENDING-based template: GET /schema only serves schemas for pending
    # node shapes (TASK handlers expose no schema provider).
    repo.save(NodeDefinition(
        id="nd-input", tenant_id="t1", name="Templated Input",
        base_type=NodeType.INPUT,
        input_schema=_DEFN_INPUT_SCHEMA,
        data_mode="json", edit_mode="none",
    ))
    return c


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _save_workflow(client, name="wf-biz", definition_id="nd-1",
                   node_type="TASK"):
    return assert_ok(client.post("/api/v1/workflows", json={
        "name": name,
        "nodes": [
            {"id": "t1", "name": "T", "node_type": "TRIGGER",
             "parameters": {"trigger_type": "manual"}},
            {"id": "biz", "name": "Biz", "node_type": node_type,
             "node_definition_id": definition_id,
             "parameters": {"note": "hello"}},
        ],
        "edges": [{"source_node_id": "t1", "target_node_id": "biz"}],
    }))


async def _drain(container):
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


# ── D1: enrichment writes the snapshot, config stays clean ──


def test_save_writes_definition_snapshot(client, container):
    created = _save_workflow(client)
    node = container["_infra"].repos.workflow.get_by_id(created["id"]).get_node("biz")

    snap = node.definition_snapshot
    assert snap is not None, "enrichment must populate definition_snapshot"
    assert snap["locked_defaults"] == {"method": "GET"}
    assert snap["input_mapping"] == {"data_ref": "{{$custom.note}}"}
    assert snap["output_mapping"] == {"summary": "{{$_output.status_code}}"}
    assert snap["input_schema"] == _DEFN_INPUT_SCHEMA
    assert snap["output_schema"][0]["key"] == "summary"
    assert snap["data_mode"] == "csv_single"
    assert snap["edit_mode"] == "before_dispatch"
    assert snap["base_worker_id"] == "http-worker"
    assert node.worker_type == "http-worker"
    assert not any(k.startswith("_") for k in node.config), (
        "nothing may be smuggled into config any more"
    )


# ── D2: dispatch applies locked_defaults + input_mapping from snapshot ──


@pytest.mark.asyncio
async def test_dispatch_applies_snapshot_defaults_and_mapping(client, container):
    created = _save_workflow(client, name="wf-biz-run",
                             definition_id="nd-json")
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=created["id"], input_data={}),
    )
    assert not result.error, result.error
    await _drain(container)

    tasks = [t for t in container["_infra"].repos.task.list_by_run(result.id)
             if t.node_id == "biz"]
    assert tasks, "business TASK was not dispatched"
    resolved = tasks[-1].resolved_input
    assert resolved["method"] == "GET"        # locked default wins
    assert resolved["data_ref"] == "hello"    # input_mapping resolved via $custom
    assert resolved["note"] == "hello"        # user binding preserved


# ── D3: completion applies output_mapping from snapshot ──


@pytest.mark.asyncio
async def test_completion_applies_snapshot_output_mapping(client, container):
    created = _save_workflow(client, name="wf-biz-complete",
                             definition_id="nd-json")
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=created["id"], input_data={}),
    )
    assert not result.error, result.error
    await _drain(container)
    run_id = result.id

    tasks = [t for t in container["_infra"].repos.task.list_by_run(run_id)
             if t.node_id == "biz"]
    assert tasks
    complete = await container["complete_task"].execute(CompleteTaskCommand(
        run_id=run_id, task_id=tasks[-1].id, status="COMPLETED",
        output_reference="file-ref-1",
        output_metadata={"status_code": 201, "body": {"x": 1}},
    ))
    assert complete.completed, complete.error
    await _drain(container)

    view = container["run_context_view_store"].get(run_id)
    entry = view.variables.get("biz") or {}
    columns = entry.get("columns") or []
    assert any("summary" in str(c) for c in columns), (
        f"output_mapping must reshape the node output to its mapped keys; "
        f"got columns={columns}"
    )


# ── D4: GET /schema serves the enrichment-produced snapshot schema ──


def test_get_schema_serves_enriched_snapshot(client, container):
    """PENDING business node: the snapshot the enrichment wrote (not a
    fixture) feeds GET /schema's design-time level. TASK-shaped nodes
    expose no schema provider, hence the INPUT-based template."""
    created = _save_workflow(client, name="wf-biz-schema",
                             definition_id="nd-input", node_type="INPUT")
    workflow = container["_infra"].repos.workflow.get_by_id(created["id"])
    assert workflow.get_node("biz").definition_snapshot, "enrichment ran"

    run = WorkflowRun(
        id="r-biz", workflow_id=workflow.id, status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    )
    container["get_node_schema"].run_repo.save(run)
    task = Task(id="t-biz", run_id="r-biz", node_id="biz",
                worker_type="", status=TaskStatus.PENDING,
                created_at="2026-01-01T00:00:00Z")
    task.output_metadata = {}
    container["get_node_schema"].task_repo.save(task)

    r = client.get("/api/v1/runs/r-biz/nodes/biz/schema")
    data = assert_ok(r)
    assert data["schema"] == _DEFN_INPUT_SCHEMA
    assert data["schema_source"] == "design_time"


# ── D5: GET node-execution serves snapshot edit/data modes ──


@pytest.mark.asyncio
async def test_node_execution_serves_snapshot_modes(client, container):
    created = _save_workflow(client, name="wf-biz-exec")
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=created["id"], input_data={}),
    )
    assert not result.error, result.error
    await _drain(container)

    r = client.get(f"/api/v1/runs/{result.id}/nodes/biz/execution")
    data = assert_ok(r)
    assert data["edit_mode"] == "before_dispatch"
    assert data["data_mode"] == "csv_single"


# ── D6: re-save refreshes the snapshot from the edited definition ──


def test_resave_refreshes_snapshot(client, container):
    created = _save_workflow(client, name="wf-biz-refresh")
    container["_infra"].repos.node_definition.save(
        _definition(locked_defaults={"method": "POST"}),
    )
    r = client.patch(f"/api/v1/workflows/{created['id']}", json={
        "nodes": [
            {"id": "t1", "name": "T", "node_type": "TRIGGER",
             "parameters": {"trigger_type": "manual"}},
            {"id": "biz", "name": "Biz", "node_type": "TASK",
             "node_definition_id": "nd-1",
             "parameters": {"note": "hello"}},
        ],
    })
    assert_ok(r)
    node = container["_infra"].repos.workflow.get_by_id(created["id"]).get_node("biz")
    assert node.definition_snapshot["locked_defaults"] == {"method": "POST"}
