"""Integration test for GET /runs/{run_id}/nodes/{node_id}/variable-suggestions.

Verifies the FE contract end-to-end through FastAPI:
- All reachable columns + nested child columns are emitted as VariablePath entries
- expression_prefix + path composes into {{$<node>.<key>...}} for every kind
- preview_rows have _row_index stripped, columns preserves CSV order
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok


# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

WORKFLOW_ID = "wf-vs-1"
RUN_ID = "run-vs-1"


# ---------------------------------------------------------------------------
# Output schemas — one node per path-kind family
# ---------------------------------------------------------------------------

# nA: scalars + nested object (covers `scalar` kind on both leaves and parents).
SCHEMA_NA = [
    {"key": "customer_id", "outputType": "string", "fieldConfig": {}},
    {
        "key": "address",
        "outputType": "object",
        "fieldConfig": {"fieldWrapper": "DivWrapper"},
        "fields": [
            {"key": "city", "outputType": "string", "fieldConfig": {}},
            {"key": "zip", "outputType": "string", "fieldConfig": {}},
        ],
    },
]

# nB: artifact-bound table with a nested file_ref column
# (covers `artifact`, `artifact_row`, `artifact_column`, plus inline `file_ref`).
SCHEMA_NB = [
    {
        "key": "orders",
        "outputType": "array",
        "fieldConfig": {
            "fieldWrapper": "TableWrapper",
            "wrapperProps": {"artifact_binding": "orders"},
        },
        "fields": [
            {"key": "sku", "outputType": "string", "fieldConfig": {}},
            {"key": "qty", "outputType": "number", "fieldConfig": {}},
            {
                "key": "attachment",
                "outputType": "object",
                "fieldConfig": {"fieldControl": "UploadControl"},
            },
        ],
    },
]

# nC: a bare file_ref scalar (covers the auto-expanded `file_ref` kind on its own).
SCHEMA_NC = [
    {
        "key": "report",
        "outputType": "object",
        "fieldConfig": {"fieldControl": "UploadControl"},
    },
]

# Workflow-level input schema.
INPUT_SCHEMA = [
    {"key": "customer_id", "outputType": "string", "fieldConfig": {}},
    {"key": "region", "outputType": "string", "fieldConfig": {}},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


@pytest.fixture
def file_client(container):
    return container["file_service_client"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path_tuples(paths: list[dict]) -> set[tuple[str, str, str]]:
    return {(p["path"], p["kind"], p["output_type"]) for p in paths}


def _by_path(paths: list[dict]) -> dict[str, dict]:
    return {p["path"]: p for p in paths}


def _scope(data: dict, scope_type: str, scope_id: str | None = None) -> dict | None:
    for s in data["scopes"]:
        if s["scope_type"] == scope_type and (scope_id is None or s["scope_id"] == scope_id):
            return s
    return None


def _node_scopes(data: dict) -> list[dict]:
    return [s for s in data["scopes"] if s["scope_type"] == "node"]


def _find_source(data: dict, node_id: str) -> dict:
    matches = [s for s in _node_scopes(data) if s["scope_id"] == node_id]
    assert matches, (
        f"node scope {node_id!r} not found in "
        f"{[s['scope_id'] for s in _node_scopes(data)]}"
    )
    return matches[0]


def _compose(source: dict, path: str) -> str:
    return f"{source['expression_prefix']}.{path}}}}}"


def _make_workflow() -> Workflow:
    nA = WorkflowNode(
        id="nA", name="NodeA", node_type=NodeType.TASK,
        definition_snapshot={"output_schema": SCHEMA_NA},
    )
    nB = WorkflowNode(
        id="nB", name="NodeB", node_type=NodeType.TASK,
        definition_snapshot={"output_schema": SCHEMA_NB},
    )
    nC = WorkflowNode(
        id="nC", name="NodeC", node_type=NodeType.TASK,
        definition_snapshot={"output_schema": SCHEMA_NC},
    )
    nTarget = WorkflowNode(
        id="nTarget", name="NodeTarget", node_type=NodeType.TASK,
    )
    return Workflow(
        id=WORKFLOW_ID, name="Variable Suggestions Test",
        nodes=[nA, nB, nC, nTarget],
        edges=[
            WorkflowEdge(id="e1", source_node_id="nA", target_node_id="nB"),
            WorkflowEdge(id="e2", source_node_id="nB", target_node_id="nC"),
            WorkflowEdge(id="e3", source_node_id="nC", target_node_id="nTarget"),
        ],
        input_schema=INPUT_SCHEMA,
    )


async def _seed(container, file_client) -> tuple[str, str, str]:
    """Seed the workflow + run; stage CSV bytes via the mock file service.

    Returns (orders_file_id, report_file_id, invoice_file_id).
    """
    # Stage CSV content. Header order locks the `columns` order assertion.
    orders_fid = (await file_client.upload_file(
        b"sku,qty,attachment\nABC,3,att-1\nXYZ,7,att-2\n",
        "orders.csv",
    ))["file_id"]
    report_fid = (await file_client.upload_file(
        b"report-bytes", "report.pdf", "application/pdf",
    ))["file_id"]
    invoice_fid = (await file_client.upload_file(
        b"x", "invoice.pdf", "application/pdf",
    ))["file_id"]

    wf = _make_workflow()
    container["get_variable_suggestions"].workflow_repo.save(wf)

    run = WorkflowRun(id=RUN_ID, workflow_id=WORKFLOW_ID, status=RunStatus.RUNNING)
    run.context.variables["input"] = {"customer_id": "C-42", "region": "EU"}
    run.context.variables["nA"] = {
        "customer_id": "C-42",
        "address": {"city": "Munich", "zip": "80331"},
    }
    run.context.variables["nB"] = build_file_ref(
        file_id=orders_fid,
        content_type="text/csv",
        row_count=2,
        columns=["sku", "qty", "attachment"],
    )
    run.context.variables["nC"] = build_file_ref(
        file_id=report_fid,
        content_type="application/pdf",
        row_count=0,
        columns=[],
    )
    run.context.variables["artifacts"] = {
        "invoice_pdf": {
            "file_id": invoice_fid,
            "filename": "invoice.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
            "produced_by_node": "nC",
            "produced_by_task": "t-1",
            "registered_at": "2026-04-29T10:00:00+00:00",
        },
    }
    container["get_variable_suggestions"].run_repo.save(run)
    return orders_fid, report_fid, invoice_fid


# ---------------------------------------------------------------------------
# Test 1 — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_variable_suggestions_lists_all_columns_and_expression_prefixes(
    client, container, file_client,
):
    orders_fid, report_fid, invoice_fid = await _seed(container, file_client)

    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/nTarget/variable-suggestions")
    data = assert_ok(resp)

    # ── Top level ──────────────────────────────────────────────────────────
    assert data["run_id"] == RUN_ID
    assert data["node_id"] == "nTarget"
    assert data["error"] is None
    assert data["error_code"] == ""
    assert data["workflow"]["id"] == WORKFLOW_ID
    assert data["run"]["id"] == RUN_ID
    assert data["run"]["status"] == "RUNNING"

    # ── $input scope ───────────────────────────────────────────────────────
    inp = _scope(data, "input")
    assert inp is not None
    assert inp["expression_prefix"] == "{{$input"
    assert inp["scope_id"] == "input"
    inp_paths = _by_path(inp["paths"])
    assert inp_paths["customer_id"]["kind"] == "scalar"
    assert inp_paths["customer_id"]["output_type"] == "string"
    assert inp_paths["customer_id"]["sample"] == "C-42"
    assert inp_paths["region"]["sample"] == "EU"

    # ── node scopes ordering — target must NOT be present ─────────────────
    assert [s["scope_id"] for s in _node_scopes(data)] == ["nA", "nB", "nC"]

    # ── nA: scalars + nested object ────────────────────────────────────────
    nA = _find_source(data, "nA")
    assert nA["expression_prefix"] == "{{$nA"
    assert nA["node_type"] == "TASK"
    assert nA["has_file_data"] is False
    assert nA["columns"] == []
    assert nA["preview_rows"] == []
    nA_tuples = _path_tuples(nA["paths"])
    assert ("customer_id", "scalar", "string") in nA_tuples
    assert ("address", "scalar", "object") in nA_tuples
    assert ("address.city", "scalar", "string") in nA_tuples
    assert ("address.zip", "scalar", "string") in nA_tuples
    nA_by = _by_path(nA["paths"])
    assert nA_by["address.city"]["sample"] == "Munich"

    # ── nB: file-backed CSV + artifact table with nested file_ref column ──
    nB = _find_source(data, "nB")
    assert nB["expression_prefix"] == "{{$nB"
    assert nB["has_file_data"] is True
    assert nB["file_id"] == orders_fid

    # FE-critical: CSV column order locked, _row_index stripped.
    assert nB["columns"] == ["sku", "qty", "attachment"]
    assert len(nB["preview_rows"]) == 2
    for row in nB["preview_rows"]:
        assert "_row_index" not in row, f"_row_index leaked into preview row: {row}"
    assert nB["preview_rows"][0] == {"sku": "ABC", "qty": "3", "attachment": "att-1"}

    nB_tuples = _path_tuples(nB["paths"])
    expected_nB = {
        ("orders", "artifact", "array"),
        ("orders[*]", "artifact_row", "object"),
        ("orders[*].sku", "artifact_column", "string"),
        ("orders[*].qty", "artifact_column", "number"),
        ("orders[*].attachment", "file_ref", "object"),
        ("orders[*].attachment.file_id", "scalar", "string"),
        ("orders[*].attachment.filename", "scalar", "string"),
        ("orders[*].attachment.content_type", "scalar", "string"),
        ("orders[*].attachment.size_bytes", "scalar", "number"),
    }
    missing_nB = expected_nB - nB_tuples
    assert not missing_nB, f"nB missing path tuples: {missing_nB}"

    # Meta paths from _meta_paths_for_file_ref.
    nB_paths = {p["path"] for p in nB["paths"]}
    for meta in ("__file_id", "__row_count", "__columns", "__content_type"):
        assert meta in nB_paths, f"nB missing meta path {meta}"

    # ── nC: bare file_ref scalar with 4 auto-expanded sub-paths ────────────
    nC = _find_source(data, "nC")
    assert nC["expression_prefix"] == "{{$nC"
    assert nC["has_file_data"] is True
    assert nC["file_id"] == report_fid
    nC_tuples = _path_tuples(nC["paths"])
    expected_nC = {
        ("report", "file_ref", "object"),
        ("report.file_id", "scalar", "string"),
        ("report.filename", "scalar", "string"),
        ("report.content_type", "scalar", "string"),
        ("report.size_bytes", "scalar", "number"),
    }
    missing_nC = expected_nC - nC_tuples
    assert not missing_nC, f"nC missing path tuples: {missing_nC}"

    # ── $artifacts scope ───────────────────────────────────────────────────
    artifacts = _scope(data, "artifacts")
    assert artifacts is not None
    assert artifacts["expression_prefix"] == "{{$artifacts"
    assert artifacts["scope_id"] == "artifacts"
    art_by = _by_path(artifacts["paths"])
    assert art_by["invoice_pdf"]["kind"] == "file_ref"
    assert art_by["invoice_pdf.file_id"]["sample"] == invoice_fid
    for sub in ("filename", "content_type", "size_bytes"):
        assert f"invoice_pdf.{sub}" in art_by, f"missing invoice_pdf.{sub}"

    # ── Expression-mapping contract ────────────────────────────────────────
    expression_cases = [
        (inp,        "customer_id",                     "{{$input.customer_id}}"),
        (nA,         "address.city",                    "{{$nA.address.city}}"),
        (nB,         "orders[*].sku",                   "{{$nB.orders[*].sku}}"),
        (nB,         "orders[*].attachment.file_id",    "{{$nB.orders[*].attachment.file_id}}"),
        (nC,         "report.file_id",                  "{{$nC.report.file_id}}"),
        (artifacts,  "invoice_pdf.filename",            "{{$artifacts.invoice_pdf.filename}}"),
    ]
    for src, path, expected in expression_cases:
        assert path in _by_path(src["paths"]), (
            f"expected path {path!r} on scope "
            f"{src.get('label') or src['expression_prefix']}"
        )
        assert _compose(src, path) == expected
        # Each path also carries the pre-composed display_path now.
        assert _by_path(src["paths"])[path]["display_path"] == expected


# ---------------------------------------------------------------------------
# Test 2 — run not found
# ---------------------------------------------------------------------------

def test_variable_suggestions_run_not_found(client):
    resp = client.get("/api/v1/runs/no-such-run/nodes/anything/variable-suggestions")
    data = assert_ok(resp)
    assert data["error_code"] == "RUN_NOT_FOUND"
    assert data["error"]
    assert data["scopes"] == []
    # Legacy keys are gone (hard cutover).
    assert "sources" not in data
    assert "input" not in data
    assert "artifacts" not in data


# ---------------------------------------------------------------------------
# Test 3 — target node missing from workflow → fallback returns all nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_variable_suggestions_target_not_in_workflow_returns_all_sources(
    client, container, file_client,
):
    await _seed(container, file_client)
    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/nGhost/variable-suggestions")
    data = assert_ok(resp)
    assert {s["scope_id"] for s in _node_scopes(data)} == {"nA", "nB", "nC", "nTarget"}


# ---------------------------------------------------------------------------
# Test 4 — system scopes always present (run, workflow, variables)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_scopes_run_workflow_variables_always_present(
    client, container, file_client,
):
    await _seed(container, file_client)
    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/nTarget/variable-suggestions")
    data = assert_ok(resp)

    types = [s["scope_type"] for s in data["scopes"]]
    # Ordering: run → workflow → variables → input → node × N → artifacts
    assert types[0] == "run"
    assert types[1] == "workflow"
    assert types[2] == "variables"

    run_scope = _scope(data, "run")
    assert run_scope["expression_prefix"] == "{{$run"
    run_paths = _by_path(run_scope["paths"])
    assert run_paths["id"]["sample"] == RUN_ID
    assert run_paths["id"]["display_path"] == "{{$run.id}}"
    assert run_paths["workflow_id"]["sample"] == WORKFLOW_ID

    wf_scope = _scope(data, "workflow")
    assert wf_scope["expression_prefix"] == "{{$workflow"
    wf_paths = _by_path(wf_scope["paths"])
    assert wf_paths["id"]["sample"] == WORKFLOW_ID

    vars_scope = _scope(data, "variables")
    # Empty registry → empty paths but scope still present (discoverability hint).
    assert vars_scope is not None
    assert vars_scope["expression_prefix"] == "{{$variables"


# ---------------------------------------------------------------------------
# Test 5 — variables scope reflects __vars and display_path is correct
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_variables_scope_reflects_set_variable_writes(
    client, container, file_client,
):
    await _seed(container, file_client)
    # Simulate a SET_VARIABLE having written __vars during the run.
    run = container["get_variable_suggestions"].run_repo.get_by_id(RUN_ID)
    run.context.variables["__vars"] = {"userCount": 42, "label": "active"}
    container["get_variable_suggestions"].run_repo.save(run)

    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/nTarget/variable-suggestions")
    data = assert_ok(resp)

    vars_scope = _scope(data, "variables")
    assert vars_scope is not None
    by_path = _by_path(vars_scope["paths"])
    assert set(by_path.keys()) == {"userCount", "label"}
    assert by_path["userCount"]["sample"] == 42
    assert by_path["userCount"]["output_type"] == "number"
    assert by_path["userCount"]["display_path"] == "{{$variables.userCount}}"
    assert by_path["label"]["display_path"] == "{{$variables.label}}"
