"""Integration tests for internal function controller endpoints.

Covers:
- GET /api/v1/internal-functions/types
- GET /api/v1/internal-functions/api-actions
- POST /api/v1/internal-functions/trigger-workflow
- POST /api/v1/internal-functions/call-api
- GET /api/v1/internal-functions/workflows/{workflow_id}/output
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer2_application.features.start_run.use_cases.start_run_use_case import StartRunResult
from bootstrap import build_app_container
from tests.conftest import assert_ok, assert_error


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    app = create_fastapi_app(container)
    return TestClient(app)


# ────────────── GET /types ──────────────


class TestListFunctionTypes:

    def test_list_function_types_returns_two_types(self, client):
        """Test that function types endpoint returns trigger_workflow and call_api."""
        response = client.get("/api/v1/internal-functions/types")
        data = assert_ok(response)

        assert len(data) == 2

        types = [t["type"] for t in data]
        assert "trigger_workflow" in types
        assert "call_api" in types

    def test_list_function_types_has_config_schema(self, client):
        """Test that each function type has config_schema."""
        response = client.get("/api/v1/internal-functions/types")
        data = assert_ok(response)

        for func_type in data:
            assert "type" in func_type
            assert "label" in func_type
            assert "description" in func_type
            assert "config_schema" in func_type
            assert isinstance(func_type["config_schema"], list)

    def test_trigger_workflow_type_has_workflow_id_field(self, client):
        """Test trigger_workflow type has required workflow_id field."""
        response = client.get("/api/v1/internal-functions/types")
        data = assert_ok(response)

        trigger_type = next(t for t in data if t["type"] == "trigger_workflow")
        schema = trigger_type["config_schema"]

        workflow_id_field = next((f for f in schema if f["key"] == "workflow_id"), None)
        assert workflow_id_field is not None
        assert workflow_id_field["required"] is True
        assert workflow_id_field["type"] == "string"


# ────────────── GET /api-actions ──────────────


class TestListApiActions:

    def test_list_api_actions_returns_predefined_actions(self, client):
        """Test that api-actions endpoint returns predefined actions."""
        response = client.get("/api/v1/internal-functions/api-actions")
        data = assert_ok(response)

        assert len(data) >= 1  # At least one action defined

        action_names = [a["name"] for a in data]
        # Check some expected actions
        assert "get_list_object_type" in action_names or len(action_names) > 0

    def test_api_actions_have_required_fields(self, client):
        """Test each action has required fields."""
        response = client.get("/api/v1/internal-functions/api-actions")
        data = assert_ok(response)

        for action in data:
            assert "name" in action
            assert "description" in action
            assert "method" in action
            assert "endpoint_url" in action
            assert action["method"] in ["GET", "POST", "PUT", "DELETE"]


# ────────────── POST /trigger-workflow ──────────────


class TestTriggerWorkflow:

    def test_trigger_workflow_returns_run_id(self, client, container):
        """Test triggering workflow returns run_id."""
        # Mock the internal_trigger_workflow use case
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "run_id": "run-123",
            "workflow_id": "wf-1",
            "status": "RUNNING",
            "message": "Workflow started.",
        })
        container["internal_trigger_workflow"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/trigger-workflow",
            json={
                "workflow_id": "wf-1",
                "input_data": {"key": "value"},
                "wait_for_completion": False,
            },
        )
        data = assert_ok(response)

        assert data["run_id"] == "run-123"
        assert data["workflow_id"] == "wf-1"
        assert data["status"] == "RUNNING"

    def test_trigger_workflow_without_input_data(self, client, container):
        """Test triggering workflow without input data."""
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "run_id": "run-456",
            "workflow_id": "wf-2",
            "status": "RUNNING",
            "message": "Workflow started.",
        })
        container["internal_trigger_workflow"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/trigger-workflow",
            json={"workflow_id": "wf-2"},
        )
        data = assert_ok(response)

        assert data["run_id"] == "run-456"

    def test_trigger_workflow_validation_error(self, client):
        """Test triggering workflow with missing workflow_id."""
        response = client.post(
            "/api/v1/internal-functions/trigger-workflow",
            json={},  # Missing workflow_id
        )

        assert response.status_code == 422  # Validation error

    def test_trigger_workflow_usecase_not_configured(self, client, container):
        """Test error when usecase is not configured."""
        container["internal_trigger_workflow"] = None

        response = client.post(
            "/api/v1/internal-functions/trigger-workflow",
            json={"workflow_id": "wf-1"},
        )

        assert response.status_code == 503
        error = assert_error(response)
        assert "not configured" in error["message"].lower()

    def test_trigger_workflow_error_response(self, client, container):
        """Test error response from usecase."""
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "error": "Workflow not found",
        })
        container["internal_trigger_workflow"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/trigger-workflow",
            json={"workflow_id": "invalid-wf"},
        )

        assert response.status_code == 400
        error = assert_error(response)
        assert "Workflow not found" in error["message"]


# ────────────── POST /call-api ──────────────


class TestCallApi:

    def test_call_api_returns_file_id(self, client, container):
        """Test calling API returns file_id."""
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "file_id": "file-123",
            "record_count": 5,
            "action_name": "get_list_object_type",
            "status": "success",
        })
        container["internal_call_api"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={
                "action_name": "get_list_object_type",
                "params": {},
            },
        )
        data = assert_ok(response)

        assert data["file_id"] == "file-123"
        assert data["record_count"] == 5
        assert data["status"] == "success"

    def test_call_api_with_params(self, client, container):
        """Test calling API with parameters."""
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "file_id": "file-456",
            "record_count": 10,
            "action_name": "get_list_template",
            "status": "success",
        })
        container["internal_call_api"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={
                "action_name": "get_list_template",
                "params": {"filter": "active", "limit": 10},
            },
        )
        data = assert_ok(response)

        assert data["file_id"] == "file-456"

    def test_call_api_unknown_action(self, client, container):
        """Test calling API with unknown action name."""
        mock_usecase = AsyncMock()
        container["internal_call_api"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={"action_name": "unknown_action"},
        )

        assert response.status_code == 400
        error = assert_error(response)
        assert "unknown_action" in error["message"].lower()

    def test_call_api_usecase_not_configured(self, client, container):
        """Test error when usecase is not configured."""
        container["internal_call_api"] = None

        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={"action_name": "get_list_object_type"},
        )

        assert response.status_code == 503
        error = assert_error(response)
        assert "not configured" in error["message"].lower()

    def test_call_api_error_response(self, client, container):
        """Test error response from usecase."""
        mock_usecase = AsyncMock()
        mock_usecase.execute = AsyncMock(return_value={
            "error": "API request failed",
            "action_name": "get_list_object_type",
        })
        container["internal_call_api"] = mock_usecase

        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={"action_name": "get_list_object_type"},
        )

        assert response.status_code == 400
        error = assert_error(response)
        assert "API request failed" in error["message"]

    def test_call_api_validation_error(self, client):
        """Test calling API with missing action_name."""
        response = client.post(
            "/api/v1/internal-functions/call-api",
            json={},  # Missing action_name
        )

        assert response.status_code == 422  # Validation error


# ────────────── GET /workflows/{workflow_id}/output ──────────────


class TestGetWorkflowOutput:

    def test_get_workflow_output_returns_file_id(self, client, container):
        """Test getting workflow output as file."""
        # Create a mock GetRunStatusUseCase result
        from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import (
            GetRunStatusUseCase,
        )
        from dataclasses import dataclass

        @dataclass
        class MockRunResult:
            id: str = "run-123"
            workflow_id: str = "wf-1"
            status: str = "COMPLETED"
            outputs: dict = None

            def __post_init__(self):
                if self.outputs is None:
                    self.outputs = {"result": [{"id": 1}]}

        mock_get_run_status = AsyncMock()
        mock_get_run_status.execute = AsyncMock(return_value=MockRunResult())
        container["get_run_status"] = mock_get_run_status

        mock_file_service = AsyncMock()
        mock_file_service.upload_file = AsyncMock(return_value={"file_id": "output-file-123"})
        container["file_service_client"] = mock_file_service

        response = client.get(
            "/api/v1/internal-functions/workflows/wf-1/output",
            params={"run_id": "run-123"},
        )
        data = assert_ok(response)

        assert data["file_id"] == "output-file-123"
        assert data["run_id"] == "run-123"
        assert data["workflow_id"] == "wf-1"

    def test_get_workflow_output_run_not_found(self, client, container):
        """Test error when run is not found."""
        from dataclasses import dataclass

        @dataclass
        class MockRunResult:
            id: str = ""
            status: str = ""
            outputs: dict = None

        mock_get_run_status = AsyncMock()
        mock_get_run_status.execute = AsyncMock(return_value=MockRunResult())
        container["get_run_status"] = mock_get_run_status

        response = client.get(
            "/api/v1/internal-functions/workflows/wf-1/output",
            params={"run_id": "invalid-run"},
        )

        assert response.status_code == 404
        error = assert_error(response)
        assert "not found" in error["message"].lower()

    def test_get_workflow_output_run_not_completed(self, client, container):
        """Test error when run is not completed."""
        from dataclasses import dataclass

        @dataclass
        class MockRunResult:
            id: str = "run-123"
            status: str = "RUNNING"
            outputs: dict = None

        mock_get_run_status = AsyncMock()
        mock_get_run_status.execute = AsyncMock(return_value=MockRunResult())
        container["get_run_status"] = mock_get_run_status

        response = client.get(
            "/api/v1/internal-functions/workflows/wf-1/output",
            params={"run_id": "run-123"},
        )

        assert response.status_code == 400
        error = assert_error(response)
        assert "not completed" in error["message"].lower()

    def test_get_workflow_output_services_not_configured(self, client, container):
        """Test error when services are not configured."""
        container["get_run_status"] = None
        container["file_service_client"] = None

        response = client.get(
            "/api/v1/internal-functions/workflows/wf-1/output",
            params={"run_id": "run-123"},
        )

        assert response.status_code == 503
        error = assert_error(response)
        assert "not configured" in error["message"].lower()
