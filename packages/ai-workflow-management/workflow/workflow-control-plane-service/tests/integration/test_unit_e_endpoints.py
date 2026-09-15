"""Integration tests for Unit E endpoints.

Covers the four new HTTP routes end-to-end with TestClient:

- ``POST /nodes/{id}/submit``           — submit_node
- ``GET  /nodes/{id}/artifacts/{aid}/odata`` — query_node_artifact
- ``POST /runs/{id}/rerun``             — rerun_run
- ``POST /runs/{id}/resolve``           — resolve_variable_paths
"""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.data_reference import (
    add_artifact, build_file_ref, register_artifacts,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer1_domain.value_objects.csv_document import CsvDocument, ROW_ID_COLUMN
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_error, assert_ok


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _make_csv(header, rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=header)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in header})
    return out.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# POST /submit
# ---------------------------------------------------------------------------

async def _seed_pending_input(container):
    """Seed a minimal INPUT node in PENDING state so submit can complete it."""
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo
    task_repo = container["get_node_schema"].task_repo

    wf = Workflow(
        id="wf-e1", name="T",
        nodes=[WorkflowNode(
            id="n1", name="N1", node_type=NodeType.INPUT,
            config={
                "_node_definition": {
                    "input_schema": {"fields": [
                        {"key": "name", "outputType": "string", "fieldConfig": {}},
                    ]},
                },
            },
        )],
        edges=[],
    )
    wf_repo.save(wf)

    run = WorkflowRun(
        id="r-e1", workflow_id="wf-e1",
        status=RunStatus.RUNNING,
        context=RunContext(
            variables={"n1": {}},
            topology=GraphTopology(
                adjacency={"n1": []}, predecessors={"n1": []},
                edge_map={}, entry_nodes=["n1"], leaf_nodes=["n1"],
            ),
        ),
    )
    run_repo.save(run)

    task = Task(
        id="t-e1", run_id="r-e1", node_id="n1",
        worker_type="", status=TaskStatus.PENDING,
        created_at="2026-01-01T00:00:00Z",
    )
    task.resolved_input = {"name": "Acme"}
    task.output_metadata = {}
    task_repo.save(task)


@pytest.mark.asyncio
async def test_submit_node_happy_path(client, container):
    await _seed_pending_input(container)
    r = client.post("/api/v1/runs/r-e1/nodes/n1/submit")
    data = assert_ok(r)
    assert data["task_id"] == "t-e1"
    assert data["node_state"] == "COMPLETED"
    assert data["downstream_dispatched"] is True


@pytest.mark.asyncio
async def test_submit_then_get_data_output_carries_file_id(client, container):
    """After submitting an INPUT node the working copy must be visible
    via ``GET /data?side=output`` — the file_ref emitted through
    ``apply_state_updates_as_files`` → ``NodeOutputProduced`` should
    land in the projector's ``variables[node_id]`` and surface as
    ``output.root.file_id`` on the subsequent GET.

    Regression for the user-reported flow: "after INPUT complete, no
    data / no file_id in /data output."
    """
    await _seed_pending_input(container)

    r1 = client.post("/api/v1/runs/r-e1/nodes/n1/submit")
    data1 = assert_ok(r1)
    assert data1["node_state"] == "COMPLETED"

    r2 = client.get("/api/v1/runs/r-e1/nodes/n1/data?side=output")
    data2 = assert_ok(r2)

    assert data2["output"] is not None, "side=output must populate output payload"
    root = data2["output"]["root"]
    assert root is not None, "root descriptor must be populated post-submit"
    assert root["file_id"], f"root.file_id must be non-empty, got {root!r}"
    # scalars dict was dropped from the payload — clients download the root
    # CSV via file_id for values.
    assert "scalars" not in data2["output"]


@pytest.mark.asyncio
async def test_submit_completes_run_when_input_is_only_leaf(client, container):
    """Regression: a workflow with a single leaf INPUT node must transition
    to RUN_COMPLETED once the user submits. Previously the NODE_COMPLETED
    topic carried an ``InputSubmitted`` payload, whose class name does not
    match the projector's ``NodeCompleted`` branch, so the node never
    landed in ``completed_nodes`` and the run stayed non-terminal forever.
    """
    from tests.helpers.queue_runner import QueueRunner
    await _seed_pending_input(container)

    r = client.post("/api/v1/runs/r-e1/nodes/n1/submit")
    assert_ok(r)

    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()

    status = client.get("/api/v1/runs/r-e1").json()["data"]["status"]
    assert status == "COMPLETED", f"run stuck at {status!r}"


@pytest.mark.asyncio
async def test_submit_node_missing_pending_task_returns_404(client, container):
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo

    wf = Workflow(id="wf-e2", name="T",
                  nodes=[WorkflowNode(id="n1", name="N1", node_type=NodeType.INPUT)],
                  edges=[])
    wf_repo.save(wf)
    run_repo.save(WorkflowRun(
        id="r-e2", workflow_id="wf-e2", status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    ))
    r = client.post("/api/v1/runs/r-e2/nodes/n1/submit")
    assert r.status_code == 404
    assert_error(r, code="TASK_NOT_FOUND")


# ---------------------------------------------------------------------------
# GET /nodes/{id}/artifacts/{aid}/odata
# ---------------------------------------------------------------------------

async def _seed_node_with_artifact(container):
    fs = container["file_service_client"]
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo

    orders_csv = CsvDocument(_make_csv(
        ["sku", "qty"],
        [{"sku": "A", "qty": "1"}, {"sku": "B", "qty": "2"}],
    )).inject_row_ids()
    up = await fs.upload_file(orders_csv, "orders.csv", "text/csv")

    ref = build_file_ref(file_id="f_main", columns=[])
    orders = add_artifact(
        ref, file_id=up["file_id"], name="orders", artifact_id="orders",
        artifact_type="array_field",
    )
    orders["columns"] = [ROW_ID_COLUMN, "sku", "qty"]

    wf_repo.save(Workflow(
        id="wf-e3", name="T",
        nodes=[WorkflowNode(id="n1", name="N1", node_type=NodeType.DATA_EDIT)],
        edges=[],
    ))
    run_repo.save(WorkflowRun(
        id="r-e3", workflow_id="wf-e3", status=RunStatus.RUNNING,
        context=RunContext(variables={"n1": ref}),
    ))
    return up["file_id"]


@pytest.mark.asyncio
async def test_query_node_artifact_odata_forwards_query(client, container):
    await _seed_node_with_artifact(container)
    r = client.get("/api/v1/runs/r-e3/nodes/n1/artifacts/orders/odata?$top=1")
    # Mock file service returns {file_id, total_matches, data: [...]}
    # OR at worst returns 200 with a body. We don't constrain the envelope
    # beyond non-error status.
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_query_node_artifact_odata_unknown_artifact_returns_404(client, container):
    await _seed_node_with_artifact(container)
    r = client.get("/api/v1/runs/r-e3/nodes/n1/artifacts/not_there/odata")
    assert r.status_code == 404
    assert_error(r, code="ARTIFACT_NOT_FOUND")


@pytest.mark.asyncio
async def test_query_node_artifact_run_not_found(client, container):
    r = client.get("/api/v1/runs/missing/nodes/n1/artifacts/orders/odata")
    assert r.status_code == 404
    assert_error(r, code="RUN_NOT_FOUND")


@pytest.mark.asyncio
async def test_query_node_artifact_resolves_via_resolved_input_fallback(client, container):
    """Working copy empty → resolve the artifact via the task's frozen
    ``resolved_input``.

    Regression for the INPUT-node "empty data" bug: a freshly-pending node's
    provisioned artifacts live on ``task.resolved_input``; the working copy
    (``view.variables[node_id]``) is empty until an edit, and isn't durably
    replayed after a cold restart. Reading only the working copy 404'd every
    artifact. The endpoint must fall back to ``resolved_input``.
    """
    fs = container["file_service_client"]
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo
    task_repo = container["get_node_schema"].task_repo

    orders_csv = CsvDocument(_make_csv(
        ["sku", "qty"], [{"sku": "A", "qty": "1"}],
    )).inject_row_ids()
    up = await fs.upload_file(orders_csv, "orders.csv", "text/csv")
    ref = build_file_ref(file_id="f_main", columns=[])
    orders = add_artifact(
        ref, file_id=up["file_id"], name="orders", artifact_id="orders",
        artifact_type="array_field",
    )
    orders["columns"] = [ROW_ID_COLUMN, "sku", "qty"]

    wf_repo.save(Workflow(
        id="wf-e3b", name="T",
        nodes=[WorkflowNode(id="n1", name="N1", node_type=NodeType.INPUT)],
        edges=[],
    ))
    # Working copy is empty — the ref lives ONLY on the task's resolved_input.
    run_repo.save(WorkflowRun(
        id="r-e3b", workflow_id="wf-e3b", status=RunStatus.RUNNING,
        context=RunContext(variables={"n1": {}}),
    ))
    task = Task(
        id="t-e3b", run_id="r-e3b", node_id="n1",
        worker_type="", status=TaskStatus.PENDING,
        created_at="2026-01-01T00:00:00Z",
    )
    task.resolved_input = ref
    task.output_metadata = {}
    task_repo.save(task)

    r = client.get("/api/v1/runs/r-e3b/nodes/n1/artifacts/orders/odata?$top=1")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /runs/{id}/rerun
# ---------------------------------------------------------------------------

async def _seed_completed_chain(container):
    """A→B linear DAG, both completed, A has a task for rerun anchor."""
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo
    task_repo = container["get_node_schema"].task_repo

    wf = Workflow(
        id="wf-e4", name="T",
        nodes=[
            WorkflowNode(id="nA", name="A", node_type=NodeType.TASK,
                         worker_type="csv-worker"),
            WorkflowNode(id="nB", name="B", node_type=NodeType.TASK,
                         worker_type="csv-worker"),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="nA", target_node_id="nB")],
    )
    wf_repo.save(wf)

    run = WorkflowRun(
        id="r-e4", workflow_id="wf-e4", status=RunStatus.COMPLETED,
        context=RunContext(
            variables={"nA": {"v": 1}, "nB": {"v": 2}},
            topology=GraphTopology(
                adjacency={"nA": ["nB"], "nB": []},
                predecessors={"nA": [], "nB": ["nA"]},
                edge_map={}, entry_nodes=["nA"], leaf_nodes=["nB"],
            ),
        ),
    )
    run.context.completed_nodes.update(["nA", "nB"])
    run_repo.save(run)

    task_a = Task(
        id="t-a", run_id="r-e4", node_id="nA",
        worker_type="csv-worker", status=TaskStatus.COMPLETED,
        created_at="2026-01-01T00:00:00Z",
    )
    task_a.resolved_input = {}
    task_a.output_metadata = {}
    task_repo.save(task_a)


@pytest.mark.asyncio
async def test_rerun_run_from_node_id_resets_downstream(client, container):
    await _seed_completed_chain(container)
    r = client.post(
        "/api/v1/runs/r-e4/rerun",
        json={"from_node_id": "nA", "rerun_strategy": "cascade"},
    )
    data = assert_ok(r)
    assert set(data["reset_node_ids"]) == {"nA", "nB"}


@pytest.mark.asyncio
async def test_rerun_run_with_variables_merges_into_context(client, container):
    await _seed_completed_chain(container)
    r = client.post(
        "/api/v1/runs/r-e4/rerun",
        json={"from_node_id": "nA", "variables": {"threshold": 0.7}},
    )
    assert_ok(r)
    run = container["get_node_schema"].run_repo.get_by_id("r-e4")
    assert run.context.variables.get("threshold") == 0.7


@pytest.mark.asyncio
async def test_rerun_run_without_from_node_uses_entry_nodes(client, container):
    await _seed_completed_chain(container)
    r = client.post("/api/v1/runs/r-e4/rerun", json={})
    data = assert_ok(r)
    # Should reset from every entry node — in this DAG that's {nA}.
    assert set(data["reset_node_ids"]) == {"nA", "nB"}


@pytest.mark.asyncio
async def test_rerun_run_unknown_from_node_returns_404(client, container):
    await _seed_completed_chain(container)
    r = client.post("/api/v1/runs/r-e4/rerun", json={"from_node_id": "missing"})
    assert r.status_code == 404
    assert_error(r, code="NODE_NOT_FOUND")


# ---------------------------------------------------------------------------
# POST /runs/{id}/resolve
# ---------------------------------------------------------------------------

async def _seed_run_with_vars(container):
    wf_repo = container["get_node_schema"].workflow_repo
    run_repo = container["get_node_schema"].run_repo
    wf_repo.save(Workflow(id="wf-e5", name="T", nodes=[], edges=[]))
    run = WorkflowRun(
        id="r-e5", workflow_id="wf-e5", status=RunStatus.RUNNING,
        context=RunContext(variables={
            "nA": {"customer_name": "Acme", "total": 1200},
            "artifacts": {
                "invoice": {
                    "file_id": "f_inv",
                    "filename": "i.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 100,
                    "produced_by_node": "nA",
                    "produced_by_task": "t",
                    "registered_at": "z",
                },
            },
        }),
    )
    run_repo.save(run)


@pytest.mark.asyncio
async def test_resolve_scalar_path(client, container):
    await _seed_run_with_vars(container)
    r = client.post(
        "/api/v1/runs/r-e5/resolve",
        json={"paths": ["{{$nA.customer_name}}", "{{$nA.total}}"]},
    )
    data = assert_ok(r)
    assert data["results"][0]["status"] == "ok"
    assert data["results"][0]["value"] == "Acme"
    assert data["results"][0]["kind"] == "scalar"
    assert data["results"][1]["value"] in ("1200", 1200)


@pytest.mark.asyncio
async def test_resolve_artifacts_file_id(client, container):
    await _seed_run_with_vars(container)
    r = client.post(
        "/api/v1/runs/r-e5/resolve",
        json={"paths": ["{{$artifacts.invoice.file_id}}"]},
    )
    data = assert_ok(r)
    assert data["results"][0]["status"] == "ok"
    assert data["results"][0]["value"] == "f_inv"


@pytest.mark.asyncio
async def test_resolve_unknown_path_errors_per_path_not_batch(client, container):
    await _seed_run_with_vars(container)
    r = client.post(
        "/api/v1/runs/r-e5/resolve",
        json={"paths": [
            "{{$nA.customer_name}}",
            "{{$nA.nonexistent_field}}",
        ]},
    )
    data = assert_ok(r)
    assert data["results"][0]["status"] == "ok"
    assert data["results"][1]["status"] == "error"
    assert "code" in (data["results"][1]["error"] or {})


@pytest.mark.asyncio
async def test_resolve_rejects_empty_paths_via_pydantic(client, container):
    await _seed_run_with_vars(container)
    r = client.post("/api/v1/runs/r-e5/resolve", json={"paths": []})
    assert r.status_code == 422  # pydantic min_length=1


@pytest.mark.asyncio
async def test_resolve_rejects_more_than_100_paths(client, container):
    await _seed_run_with_vars(container)
    r = client.post(
        "/api/v1/runs/r-e5/resolve",
        json={"paths": ["{{$nA.x}}"] * 101},
    )
    assert r.status_code == 422  # pydantic max_length=100


@pytest.mark.asyncio
async def test_resolve_run_not_found(client, container):
    r = client.post("/api/v1/runs/missing/resolve", json={"paths": ["{{$x.y}}"]})
    assert r.status_code == 404
    assert_error(r, code="RUN_NOT_FOUND")
