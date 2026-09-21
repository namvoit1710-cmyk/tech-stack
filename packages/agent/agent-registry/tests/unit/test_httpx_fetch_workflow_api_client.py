"""Unit tests for HttpxFetchWorkflowApiClient."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from app.layer4_infrastructure.external_tools.httpx_fetch_workflow_api_client import (
    HttpxFetchWorkflowApiClient,
)
from app.layer1_domain.exceptions import InvalidDataException, NotFoundException


@pytest.fixture
def mock_settings():
    settings = Mock(
        fetch_workflow_url="http://example.com/workflows/{workflow_id}",
        fetch_timeout_seconds=10,
        fetch_retry_count=1,
        fetch_backoff_seconds=0,
        fetch_min_backoff_seconds=0,
        fetch_max_backoff_seconds=1,
        retryable_exceptions=[429, 500, 502, 503, 504],
    )
    return settings


@pytest.fixture
def mock_response():
    resp = Mock()
    resp.json.return_value = {
        "status": "success",
        "data": {
            "id": "wf-1",
            "name": "test-workflow",
            "description": "A test workflow",
            "version": "1.0.0",
            "status": "active",
            "main_flow": False,
        },
    }
    return resp


def _mock_async_client(mocker, mock_response):
    """Patch httpx.AsyncClient context manager so .get returns mock_response."""
    client_instance = AsyncMock()
    client_instance.get.return_value = mock_response
    client_instance.__aenter__.return_value = client_instance
    return mocker.patch("httpx.AsyncClient", return_value=client_instance)


class TestHttpxFetchWorkflowApiClient:
    """Test the HTTPX-based workflow API client."""

    @pytest.mark.asyncio
    async def test_fetch_workflow_success(self, mock_settings, mock_response, mocker):
        """Successful fetch returns a WorkflowFetchDTO."""
        _mock_async_client(mocker, mock_response)
        client = HttpxFetchWorkflowApiClient(mock_settings)

        result = await client.fetch_workflow("wf-1")

        assert result.id == "wf-1"
        assert result.name == "test-workflow"
        assert result.description == "A test workflow"
        assert result.version == "1.0.0"
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_fetch_workflow_not_found_raises_not_found_exception(self, mock_settings, mocker):
        """404 response raises NotFoundException."""
        client_instance = AsyncMock()
        client_instance.get.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=Mock(), response=Mock(status_code=404)
        )
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)

        with pytest.raises(NotFoundException, match="Workflow with id wf-1 not found from external API"):
            await client.fetch_workflow("wf-1")

    @pytest.mark.asyncio
    async def test_fetch_workflow_http_error_raises_invalid_data(self, mock_settings, mocker):
        """Non-404 HTTP errors raise InvalidDataException."""
        client_instance = AsyncMock()
        client_instance.get.side_effect = httpx.HTTPStatusError(
            "500 Server Error", request=Mock(), response=Mock(status_code=500)
        )
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)

        with pytest.raises(InvalidDataException, match="Failed to fetch workflow data with workflow_id wf-1"):
            await client.fetch_workflow("wf-1")

        client_instance.get.assert_called_once()  # fetch_retry_count=1 → single attempt, no retry

    @pytest.mark.asyncio
    async def test_fetch_workflow_connect_error_raises_invalid_data(self, mock_settings, mocker):
        """Connection errors (subclass of HTTPError) raise InvalidDataException."""
        client_instance = AsyncMock()
        client_instance.get.side_effect = httpx.ConnectError("connection refused")
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)

        with pytest.raises(InvalidDataException, match="Failed to fetch workflow data with workflow_id wf-1"):
            await client.fetch_workflow("wf-1")

    @pytest.mark.asyncio
    async def test_fetch_workflow_validation_error_raises_invalid_data(self, mock_settings, mocker):
        """Invalid response payload raises InvalidDataException with validation message."""
        client_instance = AsyncMock()
        client_instance.get.side_effect = ValidationError.from_exception_data("test", line_errors=[])
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)

        with pytest.raises(InvalidDataException, match="Invalid workflow data received from external API with workflow_id wf-1"):
            await client.fetch_workflow("wf-1")

    @pytest.mark.asyncio
    async def test_fetch_workflow_unexpected_error_propagates(self, mock_settings, mocker):
        """Non-HTTP, non-validation exceptions propagate uncaught."""
        client_instance = AsyncMock()
        client_instance.get.side_effect = ValueError("unexpected error")
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)

        with pytest.raises(ValueError, match="unexpected error"):
            await client.fetch_workflow("wf-1")

    @pytest.mark.asyncio
    async def test_fetch_workflow_uses_timeout_from_settings(self, mock_settings, mock_response, mocker):
        """Client should forward configured timeout to httpx."""
        client_instance = AsyncMock()
        client_instance.get.return_value = mock_response
        client_instance.__aenter__.return_value = client_instance
        spy = mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(mock_settings)
        await client.fetch_workflow("wf-1")

        spy.assert_called_once_with(timeout=mock_settings.fetch_timeout_seconds)

    @pytest.mark.asyncio
    async def test_fetch_workflow_retries_on_connect_error_then_succeeds(self, mock_response, mocker):
        """ConnectError on first attempt is retried; second attempt succeeds and returns a DTO.

        retry_count=2 means stop_after_attempt(2) = 2 total attempts = 1 retry.
        """
        settings = Mock(
            fetch_workflow_url="http://example.com/workflows/{workflow_id}",
            fetch_timeout_seconds=10,
            fetch_retry_count=2,
            fetch_backoff_seconds=0,
            fetch_min_backoff_seconds=0,
            fetch_max_backoff_seconds=1,
            retryable_exceptions=[429, 500, 502, 503, 504],
        )
        client_instance = AsyncMock()
        client_instance.get.side_effect = [httpx.ConnectError("refused"), mock_response]
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(settings)
        result = await client.fetch_workflow("wf-1")

        assert client_instance.get.call_count == 2
        assert result.id == "wf-1"

    @pytest.mark.asyncio
    async def test_fetch_workflow_does_not_retry_when_retry_count_is_one(self, mocker):
        """retry_count=1 means stop_after_attempt(1) = single attempt, no retries.

        A retryable error (503) with retry_count=1 must call .get exactly once.
        """
        settings = Mock(
            fetch_workflow_url="http://example.com/workflows/{workflow_id}",
            fetch_timeout_seconds=10,
            fetch_retry_count=1,
            fetch_backoff_seconds=0,
            fetch_min_backoff_seconds=0,
            fetch_max_backoff_seconds=1,
            retryable_exceptions=[429, 500, 502, 503, 504],
        )
        client_instance = AsyncMock()
        client_instance.get.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable", request=Mock(), response=Mock(status_code=503)
        )
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(settings)

        with pytest.raises(Exception):
            await client.fetch_workflow("wf-1")

        client_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_workflow_retries_on_503_then_succeeds(self, mock_response, mocker):
        """503 is retried; a successful response on the third attempt returns a DTO.

        retry_count=3 means stop_after_attempt(3) = 3 total attempts = 2 retries.
        """
        settings = Mock(
            fetch_workflow_url="http://example.com/workflows/{workflow_id}",
            fetch_timeout_seconds=10,
            fetch_retry_count=3,
            fetch_backoff_seconds=0,
            fetch_min_backoff_seconds=0,
            fetch_max_backoff_seconds=1,
            retryable_exceptions=[429, 500, 502, 503, 504],
        )
        error_503 = httpx.HTTPStatusError(
            "503 Service Unavailable", request=Mock(), response=Mock(status_code=503)
        )
        client_instance = AsyncMock()
        client_instance.get.side_effect = [error_503, error_503, mock_response]
        client_instance.__aenter__.return_value = client_instance
        mocker.patch("httpx.AsyncClient", return_value=client_instance)

        client = HttpxFetchWorkflowApiClient(settings)
        result = await client.fetch_workflow("wf-1")

        assert client_instance.get.call_count == 3
        assert result.id == "wf-1"

