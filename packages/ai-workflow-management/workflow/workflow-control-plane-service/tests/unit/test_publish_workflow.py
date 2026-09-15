"""Unit tests for POST /api/v1/workflows/publish/{workflow_id} endpoint."""

import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer2_application.features.publish_workflow.use_cases.publish_workflow_use_case import (
    PublishWorkflowResult,
)


def _make_client(publish_workflow_mock):
    container = {
        "publish_workflow": publish_workflow_mock,
    }
    app = create_fastapi_app(container)
    return TestClient(app)


@pytest.fixture
def publish_workflow():
    return AsyncMock()


@pytest.fixture
def client(publish_workflow):
    return _make_client(publish_workflow)


class TestPublishWorkflow:
    def test_publishes_workflow_successfully(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            id="wf-1",
            name="My Workflow",
            version="2",
            description="A test workflow",
            status="PUBLISHED",
            nodes=[{"id": "n1", "name": "Start", "node_type": "TRIGGER"}],
            edges=[],
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "2", "is_confirm": False},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "wf-1"
        assert data["status"] == "PUBLISHED"
        assert data["version"] == "2"

    def test_passes_command_fields_correctly(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            id="wf-1", name="flow", version="3", status="PUBLISHED",
        )

        client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "3", "is_confirm": True},
        )

        publish_workflow.execute.assert_called_once()
        cmd = publish_workflow.execute.call_args[0][0]
        assert cmd.workflow_id == "wf-1"
        assert cmd.version == "3"
        assert cmd.is_confirm is True

    def test_returns_409_when_already_published_no_confirm(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            error="Workflow already has a published version. Confirm to overwrite.",
            error_code="WORKFLOW_ALREADY_PUBLISHED",
            error_details={
                "workflow_id": "wf-1",
                "published_version_id": "wf-1-v1",
            },
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "2", "is_confirm": False},
        )

        assert resp.status_code == 409
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "WORKFLOW_ALREADY_PUBLISHED"
        assert "published_version_id" in body["error"]["details"]

    def test_publishes_with_confirm_when_already_published(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            id="wf-1",
            name="My Workflow",
            version="2",
            status="PUBLISHED",
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "2", "is_confirm": True},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "PUBLISHED"

    def test_returns_404_when_workflow_not_found(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            error="Workflow not found",
            error_code="WORKFLOW_NOT_FOUND",
            error_details={"workflow_id": "non-existent"},
        )

        resp = client.post(
            "/api/v1/workflows/publish/non-existent",
            json={"version": "1"},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "WORKFLOW_NOT_FOUND"

    def test_returns_400_when_workflow_has_no_nodes(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            error="Invalid workflow",
            error_code="INVALID_WORKFLOW",
            error_details={"reason": "Workflow must have at least one node to publish"},
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "1"},
        )

        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "INVALID_WORKFLOW"

    def test_returns_400_when_edge_references_invalid_node(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            error="Edge references unknown node",
            error_code="INVALID_EDGE_REFERENCE",
            error_details={"edge_id": "e1", "node_id": "n-missing"},
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "1"},
        )

        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "INVALID_EDGE_REFERENCE"

    def test_default_version_and_is_confirm(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            id="wf-1", name="flow", version="", status="PUBLISHED",
        )

        client.post("/api/v1/workflows/publish/wf-1", json={})

        cmd = publish_workflow.execute.call_args[0][0]
        assert cmd.version == ""
        assert cmd.is_confirm is False

    def test_response_contains_nodes_and_edges(self, client, publish_workflow):
        publish_workflow.execute.return_value = PublishWorkflowResult(
            id="wf-1",
            name="Complex Flow",
            version="1",
            status="PUBLISHED",
            nodes=[
                {"id": "n1", "name": "Start", "node_type": "TRIGGER"},
                {"id": "n2", "name": "Task", "node_type": "TASK"},
            ],
            edges=[
                {"id": "e1", "source_node_id": "n1", "target_node_id": "n2"},
            ],
        )

        resp = client.post(
            "/api/v1/workflows/publish/wf-1",
            json={"version": "1"},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
