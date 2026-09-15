"""Integration tests for the Unit C read-side endpoints.

Covers the new routes wired to ``GetNodeSchemaUseCase``, ``GetNodeDataUseCase``,
``GetRunArtifactsUseCase``, including:

- ETag emission on 200 and ``If-None-Match`` → 304.
- Response envelope shape.
- Task_id query-param targeting.
- schema_source = design_time vs runtime.
- produced_artifacts link from ``$artifacts`` registry.
"""

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.data_reference import (
    add_artifact, build_file_ref, register_artifacts,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
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
    app = create_fastapi_app(container)
    return TestClient(app)


def _seed_workflow_and_run(container, *, node_config=None, variables=None,
                           resolved_input=None,
                           node_type=NodeType.DATA_EDIT,
                           task_status=TaskStatus.PENDING,
                           definition_snapshot=None):
    """Inject a minimal workflow/run/task trio into the in-memory repos."""
    workflow = Workflow(
        id="wf-1", name="T",
        nodes=[WorkflowNode(id="n1", name="N1",
                            node_type=node_type,
                            config=node_config or {},
                            definition_snapshot=definition_snapshot)],
        edges=[],
    )
    container["workflow_repo"].save(workflow) if "workflow_repo" in container else None
    # The bootstrap container keys aren't repositories directly; repos live on
    # the use cases. We reach in through one of the injected use cases.
    uc = container["get_node_schema"]
    uc.workflow_repo.save(workflow)
    run = WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables or {}),
    )
    uc.run_repo.save(run)
    task = Task(
        id="t-1", run_id="r-1", node_id="n1",
        worker_type="", status=task_status,
        created_at="2026-01-01T00:00:00Z",
    )
    task.resolved_input = resolved_input
    task.output_metadata = {}
    uc.task_repo.save(task)
    return workflow, run, task


# ---------------------------------------------------------------------------
# GET /schema
# ---------------------------------------------------------------------------

def test_get_schema_returns_envelope_and_etag(client, container):
    _seed_workflow_and_run(
        container,
        definition_snapshot={"input_schema": {"fields": [{"key": "a"}]}},
    )
    r = client.get("/api/v1/runs/r-1/nodes/n1/schema")
    data = assert_ok(r)
    assert data["schema"] == {"fields": [{"key": "a"}]}
    assert data["schema_source"] == "design_time"
    assert data["node_kind"] == "DATA_EDIT"
    assert r.headers.get("etag", "").startswith('W/"sha256:')


def test_get_schema_falls_back_to_declared_config_schema(client, container):
    """NodeSpec chain level 3: no runtime metadata, no definition_snapshot
    — the node's declared ``config.input_schema`` serves the form."""
    _seed_workflow_and_run(
        container,
        node_config={"input_schema": {"fields": [{"key": "from_config"}]}},
    )
    r = client.get("/api/v1/runs/r-1/nodes/n1/schema")
    data = assert_ok(r)
    assert data["schema"] == {"fields": [{"key": "from_config"}]}
    assert data["schema_source"] == "design_time"


def test_get_schema_if_none_match_returns_304(client, container):
    _seed_workflow_and_run(
        container,
        definition_snapshot={"input_schema": {"fields": [{"key": "a"}]}},
    )
    first = client.get("/api/v1/runs/r-1/nodes/n1/schema")
    etag = first.headers["etag"]

    second = client.get("/api/v1/runs/r-1/nodes/n1/schema", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


def test_get_schema_run_not_found(client, container):
    r = client.get("/api/v1/runs/missing/nodes/n1/schema")
    assert r.status_code == 404
    assert_error(r, code="RUN_NOT_FOUND")


def test_get_schema_runtime_source_for_dynamic_schema(client, container):
    task_meta = {"input_schema": {"fields": [{"key": "runtime"}]}}
    workflow, run, task = _seed_workflow_and_run(
        container,
        node_type=NodeType.DATA_EDIT,
        resolved_input=None,
    )
    task.output_metadata = task_meta
    container["get_node_schema"].task_repo.save(task)

    r = client.get("/api/v1/runs/r-1/nodes/n1/schema")
    data = assert_ok(r)
    assert data["schema_source"] == "runtime"
    assert data["schema"] == task_meta["input_schema"]


# ---------------------------------------------------------------------------
# GET /data
# ---------------------------------------------------------------------------

def _file_ref_with_scalars_and_table():
    ref = build_file_ref(
        file_id="f_main",
        columns=["customer_name"],
        version=2,
    )
    orders = add_artifact(
        ref, file_id="f_orders", name="orders", artifact_id="orders",
        artifact_type="array_field",
    )
    orders["columns"] = ["__row_id", "sku"]
    return ref


def test_get_data_returns_descriptor_without_row_count(client, container):
    ref = _file_ref_with_scalars_and_table()
    _seed_workflow_and_run(container, resolved_input=ref)

    r = client.get("/api/v1/runs/r-1/nodes/n1/data?side=input")
    data = assert_ok(r)

    # scalars dict has been dropped — all data is file-backed now.
    assert "scalars" not in data["data"]
    assert data["data"]["root"]["file_id"] == "f_main"
    assert "row_count" not in data["data"]["root"]
    assert data["data"]["root"]["children"][0]["artifact_id"] == "orders"
    assert r.headers.get("etag", "").startswith('W/"sha256:')


def test_get_data_side_both_returns_input_and_output(client, container):
    input_ref = _file_ref_with_scalars_and_table()
    output_ref = _file_ref_with_scalars_and_table()
    output_ref["file_id"] = "f_main_v2"
    _seed_workflow_and_run(
        container,
        resolved_input=input_ref,
        variables={"n1": output_ref},
    )

    r = client.get("/api/v1/runs/r-1/nodes/n1/data?side=both")
    data = assert_ok(r)
    assert data["data"]["root"]["file_id"] == "f_main"
    assert data["output"]["root"]["file_id"] == "f_main_v2"
    assert data["data"]["etag"] != data["output"]["etag"]


def test_get_data_rejects_invalid_side(client, container):
    _seed_workflow_and_run(container)
    r = client.get("/api/v1/runs/r-1/nodes/n1/data?side=garbage")
    assert r.status_code == 400


def test_get_data_if_none_match_returns_304(client, container):
    _seed_workflow_and_run(container, resolved_input=_file_ref_with_scalars_and_table())
    first = client.get("/api/v1/runs/r-1/nodes/n1/data?side=input")
    etag = first.headers["etag"]
    second = client.get("/api/v1/runs/r-1/nodes/n1/data?side=input", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_get_data_surfaces_produced_artifacts(client, container):
    ref = _file_ref_with_scalars_and_table()
    add_artifact(ref, file_id="f_evidence", name="evidence",
                 content_type="application/pdf")
    variables: dict = {}
    register_artifacts(variables, ref, node_id="n1", task_id="t-1")
    _seed_workflow_and_run(
        container, resolved_input=ref,
        variables={**variables, "n1": ref},
    )

    r = client.get("/api/v1/runs/r-1/nodes/n1/data?side=both")
    data = assert_ok(r)
    assert "evidence" in data["produced_artifacts"]


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/artifacts
# ---------------------------------------------------------------------------

def test_get_run_artifacts_returns_registry(client, container):
    ref = build_file_ref(file_id="f_main")
    add_artifact(ref, file_id="f_invoice", name="invoice_pdf",
                 content_type="application/pdf", size_bytes=100)
    _seed_workflow_and_run(container)
    # Populate the registry on the run we seeded.
    run = container["get_run_artifacts"].run_repo.get_by_id("r-1")
    register_artifacts(run.context.variables, ref, node_id="n1", task_id="t-1")
    container["get_run_artifacts"].run_repo.save(run)

    r = client.get("/api/v1/runs/r-1/artifacts")
    data = assert_ok(r)
    assert "invoice_pdf" in data["artifacts"]
    entry = data["artifacts"]["invoice_pdf"]
    assert entry["file_id"] == "f_invoice"
    assert entry["produced_by_node"] == "n1"
    assert r.headers.get("etag", "").startswith('W/"sha256:')


def test_get_run_artifacts_empty_registry(client, container):
    _seed_workflow_and_run(container)
    r = client.get("/api/v1/runs/r-1/artifacts")
    data = assert_ok(r)
    assert data["artifacts"] == {}


def test_get_run_artifacts_run_not_found(client, container):
    r = client.get("/api/v1/runs/missing/artifacts")
    assert r.status_code == 404
    assert_error(r, code="RUN_NOT_FOUND")


def test_get_run_artifacts_if_none_match_returns_304(client, container):
    _seed_workflow_and_run(container)
    first = client.get("/api/v1/runs/r-1/artifacts")
    etag = first.headers["etag"]
    second = client.get("/api/v1/runs/r-1/artifacts", headers={"If-None-Match": etag})
    assert second.status_code == 304
