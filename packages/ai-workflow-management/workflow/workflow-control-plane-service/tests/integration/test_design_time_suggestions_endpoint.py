"""Integration test for GET /workflows/{workflow_id}/nodes/{node_id}/variable-suggestions.

Design-time autocomplete: no run is seeded — suggestions come purely from
the workflow document. Verifies the FE contract end-to-end through FastAPI:
ranked flat ``matches[]``, ``?q=`` filtering, scope summaries, 404 envelope.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_error, assert_ok

WORKFLOW_ID = "wf-dts-1"

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

INPUT_SCHEMA = [
    {"key": "customer_id", "outputType": "string", "fieldConfig": {}},
    {"key": "region", "outputType": "string", "fieldConfig": {}},
]


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _make_workflow() -> Workflow:
    nA = WorkflowNode(
        id="nA", name="NodeA", node_type=NodeType.TASK,
        definition_snapshot={"output_schema": SCHEMA_NA},
    )
    nTarget = WorkflowNode(id="nTarget", name="NodeTarget", node_type=NodeType.TASK)
    return Workflow(
        id=WORKFLOW_ID, name="Design Time Suggestions Test",
        nodes=[nA, nTarget],
        edges=[WorkflowEdge(id="e1", source_node_id="nA", target_node_id="nTarget")],
        input_schema=INPUT_SCHEMA,
    )


@pytest.fixture
def seeded(container):
    container["get_design_time_variable_suggestions"].workflow_repo.save(
        _make_workflow(),
    )


def _url(node_id: str = "nTarget") -> str:
    return f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{node_id}/variable-suggestions"


def test_container_wires_use_case(container):
    assert "get_design_time_variable_suggestions" in container


def test_empty_q_returns_everything(client, seeded):
    data = assert_ok(client.get(_url()))
    assert data["workflow_id"] == WORKFLOW_ID
    assert data["node_id"] == "nTarget"
    assert data["total_matches"] == len(data["matches"]) > 0
    assert all(m["match_kind"] == "all" for m in data["matches"])
    node_matches = {m["path"]: m for m in data["matches"] if m["scope_type"] == "node"}
    assert {"customer_id", "address", "address.city", "address.zip"} <= set(node_matches)
    assert node_matches["address.city"]["display_path"] == "{{$nA.address.city}}"
    # design time: no run-only payload fields
    assert "sample" not in data["matches"][0]
    scope_types = {s["scope_type"] for s in data["scopes"]}
    assert {"run", "workflow", "variables", "input", "node", "artifacts"} <= scope_types


def test_q_filters_to_prefix_matches(client, seeded):
    # URL-encoded "{{$nA.add"
    data = assert_ok(client.get(_url(), params={"q": "{{$nA.add"}))
    assert [m["path"] for m in data["matches"]] == [
        "address", "address.zip", "address.city",
    ]
    assert all(m["match_kind"] == "prefix" for m in data["matches"])
    summaries = {s["scope_id"]: s for s in data["scopes"]}
    assert summaries["nA"]["match_count"] == 3


def test_q_source_suggestion(client, seeded):
    data = assert_ok(client.get(_url(), params={"q": "$inp"}))
    assert len(data["matches"]) == 1
    assert data["matches"][0]["match_kind"] == "source"
    assert data["matches"][0]["display_path"] == "{{$input"


def test_limit_truncates(client, seeded):
    data = assert_ok(client.get(_url(), params={"limit": 2}))
    assert len(data["matches"]) == 2
    assert data["truncated"] is True
    assert data["total_matches"] > 2


def test_unknown_workflow_404(client):
    resp = client.get(
        "/api/v1/workflows/ghost/nodes/n1/variable-suggestions",
    )
    assert resp.status_code == 404
    assert_error(resp, code="WORKFLOW_NOT_FOUND")
