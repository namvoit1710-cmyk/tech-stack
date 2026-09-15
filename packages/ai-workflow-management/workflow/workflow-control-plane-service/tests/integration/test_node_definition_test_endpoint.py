"""Integration tests for POST /api/v1/node-definitions/{id}/test.

Exercises the controller's status-code mapping end-to-end via TestClient:
  * ENGINE builtin definition → 200 with the evaluated outcome in the body.
  * PENDING definition → 422 NODE_NOT_TESTABLE_STANDALONE.
  * Unknown definition → 404.

The container wires a Hana repo by default; we swap an in-memory repo onto the
test use case so the test is hermetic (the runner/handler-registry don't depend
on the repo).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
    MemoryNodeDefinitionRepository,
)
from bootstrap import build_app_container


@pytest.fixture(scope="module")
def client_and_repo():
    container = build_app_container()
    repo = MemoryNodeDefinitionRepository()
    container["test_node_definition"].node_definition_repo = repo
    app = create_fastapi_app(container)
    return TestClient(app), repo


def _data(resp):
    body = resp.json()
    return body.get("data", body)


def test_condition_definition_returns_200_with_branch(client_and_repo):
    client, repo = client_and_repo
    repo.save(NodeDefinition(
        id="nd-cond", tenant_id="system", name="C",
        base_type=NodeType.CONDITION,
        locked_defaults={"expression": "{{$input.x}} > 5"},
    ))
    r = client.post("/api/v1/node-definitions/nd-cond/test", json={"input_data": {"x": 10}})
    assert r.status_code == 200, r.text
    body = _data(r)
    assert body["status"] == "COMPLETED"
    assert body["branch"] == "then"
    assert body["outputs"]["result"] is True


def test_input_definition_returns_422(client_and_repo):
    client, repo = client_and_repo
    repo.save(NodeDefinition(
        id="nd-input", tenant_id="system", name="I", base_type=NodeType.INPUT,
    ))
    r = client.post("/api/v1/node-definitions/nd-input/test", json={"input_data": {}})
    assert r.status_code == 422, r.text
    assert "NODE_NOT_TESTABLE_STANDALONE" in r.text


def test_missing_definition_returns_404(client_and_repo):
    client, _ = client_and_repo
    r = client.post("/api/v1/node-definitions/missing/test", json={"input_data": {}})
    assert r.status_code == 404, r.text
