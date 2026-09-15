"""Unit tests for GET /api/v1/workflows/publish/{workflow_id} endpoint."""

import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import (
    GetWorkflowResult,
)


def _make_client(get_workflow_mock):
    container = {
        "get_workflow": get_workflow_mock,
    }
    app = create_fastapi_app(container)
    return TestClient(app)


@pytest.fixture
def get_workflow():
    return AsyncMock()


@pytest.fixture
def client(get_workflow):
    return _make_client(get_workflow)


class TestGetLatestPublishWorkflow:
    def test_returns_published_workflow(self, client, get_workflow):
        get_workflow.execute.return_value = GetWorkflowResult(
            id="wf-1",
            name="My Workflow",
            version="3",
            description="Published workflow",
            status="PUBLIC",
        )

        resp = client.get("/api/v1/workflows/publish/wf-1")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "wf-1"
        assert data["name"] == "My Workflow"
        assert data["version"] == "3"
        assert data["status"] == "PUBLIC"

    def test_calls_execute_with_is_publish_true(self, client, get_workflow):
        get_workflow.execute.return_value = GetWorkflowResult(
            id="wf-1", name="flow", version="1", status="PUBLIC",
        )

        client.get("/api/v1/workflows/publish/wf-1")

        get_workflow.execute.assert_called_once()
        call_args = get_workflow.execute.call_args
        command = call_args[0][0]
        assert command.workflow_id == "wf-1"
        assert call_args[1].get("is_publish") is True

    def test_returns_404_when_no_published_version(self, client, get_workflow):
        get_workflow.execute.return_value = GetWorkflowResult(
            error="Workflow not found",
            error_code="WORKFLOW_NOT_FOUND",
        )

        resp = client.get("/api/v1/workflows/publish/non-existent")

        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "NOT_FOUND"

    def test_returns_workflow_with_nodes_and_edges(self, client, get_workflow):
        get_workflow.execute.return_value = GetWorkflowResult(
            id="wf-2",
            name="Complex Flow",
            version="1",
            description="With nodes",
            status="PUBLIC",
            nodes=[
                {"id": "n1", "name": "Start", "node_type": "TRIGGER"},
                {"id": "n2", "name": "Task", "node_type": "TASK"},
            ],
            edges=[
                {"source_node_id": "n1", "target_node_id": "n2"},
            ],
        )

        resp = client.get("/api/v1/workflows/publish/wf-2")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_returns_workflow_with_schemas(self, client, get_workflow):
        get_workflow.execute.return_value = GetWorkflowResult(
            id="wf-3",
            name="Schema Flow",
            version="2",
            status="PUBLIC",
            input_schema=[{"name": "input1", "type": "string"}],
            output_schema=[{"name": "output1", "type": "number"}],
        )

        resp = client.get("/api/v1/workflows/publish/wf-3")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["input_schema"] == [{"name": "input1", "type": "string"}]
        assert data["output_schema"] == [{"name": "output1", "type": "number"}]
