"""Unit tests for internal functions use cases.

Covers:
- InternalTriggerWorkflowUseCase
- InternalCallApiUseCase
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

from app.layer2_application.features.internal_functions import (
    InternalTriggerWorkflowUseCase,
    InternalCallApiUseCase,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
    StartRunResult,
)
from tests.conftest import DummyLogger


# ────────────── Fixtures ──────────────


@pytest.fixture
def logger():
    return DummyLogger()


@pytest.fixture
def mock_file_service():
    """Mock file service client."""
    service = AsyncMock()
    service.upload_file = AsyncMock(return_value={"file_id": "file-123"})
    return service


@pytest.fixture
def mock_start_run_usecase():
    """Mock StartRunUseCase."""
    usecase = AsyncMock()
    usecase.execute = AsyncMock(return_value=StartRunResult(
        id="run-123",
        workflow_id="wf-1",
        status="RUNNING",
    ))
    return usecase


# ────────────── InternalTriggerWorkflowUseCase ──────────────


class TestInternalTriggerWorkflowUseCase:

    @pytest.mark.asyncio
    async def test_trigger_workflow_returns_run_id_immediately(
        self, logger, mock_start_run_usecase, mock_file_service
    ):
        """Test triggering workflow without waiting returns run_id."""
        usecase = InternalTriggerWorkflowUseCase(
            logger=logger,
            start_run_usecase=mock_start_run_usecase,
            file_service_client=mock_file_service,
        )

        result = await usecase.execute(
            workflow_id="wf-1",
            input_data={"key": "value"},
            wait_for_completion=False,
        )

        assert result["run_id"] == "run-123"
        assert result["workflow_id"] == "wf-1"
        assert result["status"] == "RUNNING"
        assert "message" in result

        # Verify StartRunCommand was called correctly
        mock_start_run_usecase.execute.assert_called_once()
        call_args = mock_start_run_usecase.execute.call_args[0][0]
        assert isinstance(call_args, StartRunCommand)
        assert call_args.workflow_id == "wf-1"
        assert call_args.input_data == {"key": "value"}
        assert call_args.skip_validation is True
        assert call_args.execution_mode == "auto"

    @pytest.mark.asyncio
    async def test_trigger_workflow_with_error(
        self, logger, mock_file_service
    ):
        """Test error handling when start_run fails."""
        mock_start_run = AsyncMock()
        mock_start_run.execute = AsyncMock(return_value=StartRunResult(
            error="Workflow not found",
            error_code="NOT_FOUND",
        ))

        usecase = InternalTriggerWorkflowUseCase(
            logger=logger,
            start_run_usecase=mock_start_run,
            file_service_client=mock_file_service,
        )

        result = await usecase.execute(
            workflow_id="invalid-wf",
            wait_for_completion=False,
        )

        assert result["error"] == "Workflow not found"
        assert result["error_code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_trigger_workflow_with_empty_input(
        self, logger, mock_start_run_usecase, mock_file_service
    ):
        """Test triggering workflow with no input data."""
        usecase = InternalTriggerWorkflowUseCase(
            logger=logger,
            start_run_usecase=mock_start_run_usecase,
            file_service_client=mock_file_service,
        )

        result = await usecase.execute(
            workflow_id="wf-1",
            input_data=None,
            wait_for_completion=False,
        )

        assert result["run_id"] == "run-123"
        call_args = mock_start_run_usecase.execute.call_args[0][0]
        assert call_args.input_data == {}


# ────────────── InternalCallApiUseCase ──────────────


class TestInternalCallApiUseCase:

    @pytest.mark.asyncio
    async def test_call_api_get_request_returns_file_id(
        self, logger, mock_file_service
    ):
        """Test calling GET API and receiving file_id."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "get_list_items",
            "method": "GET",
            "endpoint_url": "https://api.example.com/items",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"id": 1, "name": "Item 1"},
                {"id": 2, "name": "Item 2"},
            ]
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["status"] == "success"
        assert result["file_id"] == "file-123"
        assert result["record_count"] == 2
        assert result["action_name"] == "get_list_items"

        # Verify file was uploaded as CSV
        mock_file_service.upload_file.assert_called_once()
        call_kwargs = mock_file_service.upload_file.call_args[1]
        assert call_kwargs["filename"] == "api_action_get_list_items_result.csv"
        assert call_kwargs["content_type"] == "text/csv"

    @pytest.mark.asyncio
    async def test_call_api_post_request(
        self, logger, mock_file_service
    ):
        """Test calling POST API."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "create_item",
            "method": "POST",
            "endpoint_url": "https://api.example.com/items",
            "payload": {"type": "default"},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 3, "name": "New Item"}
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(
                action_config=action_config,
                params={"name": "Custom Name"},
            )

        assert result["status"] == "success"
        assert result["file_id"] == "file-123"
        assert result["record_count"] == 1  # Single dict wrapped in array

    @pytest.mark.asyncio
    async def test_call_api_missing_endpoint_url(
        self, logger, mock_file_service
    ):
        """Test error when endpoint_url is missing."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "bad_action",
            "method": "GET",
            "endpoint_url": "",
            "payload": {},
        }

        result = await usecase.execute(action_config=action_config)

        assert "error" in result
        assert "bad_action" in result["error"]
        assert "endpoint_url" in result["error"]

    @pytest.mark.asyncio
    async def test_call_api_unsupported_method(
        self, logger, mock_file_service
    ):
        """Test error for unsupported HTTP method."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "patch_action",
            "method": "PATCH",
            "endpoint_url": "https://api.example.com/items/1",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert "error" in result
        assert "PATCH" in result["error"]

    @pytest.mark.asyncio
    async def test_call_api_http_error(
        self, logger, mock_file_service
    ):
        """Test handling HTTP error response."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "failing_action",
            "method": "GET",
            "endpoint_url": "https://api.example.com/error",
            "payload": {},
        }

        import httpx
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    message="Server Error",
                    request=MagicMock(),
                    response=mock_response,
                )
            )

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert "error" in result
        assert "500" in result["error"]
        assert result["action_name"] == "failing_action"

    @pytest.mark.asyncio
    async def test_call_api_relative_url_uses_base_url(
        self, logger, mock_file_service
    ):
        """Test relative URL is prepended with base URL."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "relative_action",
            "method": "GET",
            "endpoint_url": "/api/v1/items",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        # Verify the URL was constructed correctly
        call_args = mock_client_instance.get.call_args
        # The URL should include the base URL prefix
        assert "api/v1/items" in str(call_args)

    @pytest.mark.asyncio
    async def test_call_api_normalizes_paginated_response(
        self, logger, mock_file_service
    ):
        """Test that paginated response with 'data' key is normalized."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "paginated_action",
            "method": "GET",
            "endpoint_url": "https://api.example.com/items",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [{"id": 1}, {"id": 2}, {"id": 3}],
                "total": 100,
                "page": 1,
            }
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["record_count"] == 3  # Extracted from 'data' key

    @pytest.mark.asyncio
    async def test_call_api_normalizes_items_response(
        self, logger, mock_file_service
    ):
        """Test that response with 'items' key is normalized."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "items_action",
            "method": "GET",
            "endpoint_url": "https://api.example.com/collection",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "items": [{"name": "a"}, {"name": "b"}],
                "count": 2,
            }
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["record_count"] == 2  # Extracted from 'items' key

    @pytest.mark.asyncio
    async def test_call_api_file_upload_failure(
        self, logger
    ):
        """Test handling file upload failure."""
        mock_file_service = AsyncMock()
        mock_file_service.upload_file = AsyncMock(
            side_effect=Exception("Storage unavailable")
        )

        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "upload_fail_action",
            "method": "GET",
            "endpoint_url": "https://api.example.com/items",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"id": 1}]
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert "error" in result
        assert "Storage unavailable" in result["error"]
        assert result["action_name"] == "upload_fail_action"

    @pytest.mark.asyncio
    async def test_call_api_put_request(
        self, logger, mock_file_service
    ):
        """Test calling PUT API."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "update_item",
            "method": "PUT",
            "endpoint_url": "https://api.example.com/items/1",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": 1, "updated": True}
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.put = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_call_api_delete_request(
        self, logger, mock_file_service
    ):
        """Test calling DELETE API."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "delete_item",
            "method": "DELETE",
            "endpoint_url": "https://api.example.com/items/1",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_response.json.return_value = {"deleted": True}
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.delete = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_call_api_non_json_response(
        self, logger, mock_file_service
    ):
        """Test handling non-JSON response."""
        usecase = InternalCallApiUseCase(
            logger=logger,
            file_service_client=mock_file_service,
        )

        action_config = {
            "name": "text_action",
            "method": "GET",
            "endpoint_url": "https://api.example.com/text",
            "payload": {},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Plain text response"
            mock_response.json.side_effect = ValueError("No JSON")
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await usecase.execute(action_config=action_config)

        assert result["status"] == "success"
        assert result["record_count"] == 1  # raw_text wrapped in array
