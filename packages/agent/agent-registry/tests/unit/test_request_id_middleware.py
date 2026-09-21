"""Unit tests for request ID middleware."""

import uuid
import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi import Request

from app.layer4_infrastructure.middleware.request_id import RequestIDMiddleware


class TestRequestIDMiddleware:
    """Test request ID middleware."""

    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        app = Mock()
        return RequestIDMiddleware(app)

    @pytest.fixture
    def mock_request(self):
        """Create mock request."""
        request = Mock(spec=Request)
        request.url.path = "/api/test"
        request.method = "GET"
        request.query_params = {}
        request.headers = Mock()
        request.headers.get = Mock(return_value=None)
        request.state = Mock()
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create mock call_next function."""
        async def call_next(request):
            response = Mock()
            response.headers = {}
            response.status_code = 200
            return response
        return call_next

    @pytest.mark.asyncio
    async def test_generates_request_id_if_not_present(self, middleware, mock_request, mock_call_next):
        """Test that request ID is generated when not in headers."""
        mock_request.headers.get.return_value = None
        
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert hasattr(mock_request.state, "request_id")
        assert "X-Request-ID" in response.headers
        # Should be a valid UUID
        try:
            uuid.UUID(response.headers["X-Request-ID"])
            assert True
        except ValueError:
            assert False, "Request ID is not a valid UUID"

    @pytest.mark.asyncio
    async def test_uses_existing_request_id_from_header(self, middleware, mock_request, mock_call_next):
        """Test that existing request ID from header is preserved."""
        existing_id = "custom-request-id-12345"
        mock_request.headers.get.return_value = existing_id
        
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert mock_request.state.request_id == existing_id
        assert response.headers["X-Request-ID"] == existing_id

    @pytest.mark.asyncio
    async def test_adds_request_id_to_response_headers(self, middleware, mock_request, mock_call_next):
        """Test that request ID is added to response headers."""
        mock_request.headers.get.return_value = None
        
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"] == mock_request.state.request_id

    @pytest.mark.asyncio
    @patch("app.layer4_infrastructure.middleware.request_id.logger")
    async def test_logs_request_id_in_request_started(
        self, mock_logger, middleware, mock_request, mock_call_next
    ):
        """Test that request ID is included in request start log context."""
        mock_request.headers.get.return_value = None

        await middleware.dispatch(mock_request, mock_call_next)

        log_call = mock_logger.info.call_args_list[0]
        assert "request_id" in log_call.kwargs
        assert log_call.kwargs["method"] == "GET"
        assert log_call.kwargs["path"] == "/api/test"

    @pytest.mark.asyncio
    @patch("app.layer4_infrastructure.middleware.request_id.logger")
    async def test_logs_request_started(self, mock_logger, middleware, mock_request, mock_call_next):
        """Test that request started is logged."""
        mock_request.headers.get.return_value = None
        
        await middleware.dispatch(mock_request, mock_call_next)
        
        mock_logger.info.assert_any_call(
            "request_started",
            request_id=mock_request.state.request_id,
            method="GET",
            path="/api/test",
            query_params=str({}),
        )

    @pytest.mark.asyncio
    @patch("app.layer4_infrastructure.middleware.request_id.logger")
    async def test_logs_request_completed(self, mock_logger, middleware, mock_request, mock_call_next):
        """Test that successful request is logged."""
        mock_request.headers.get.return_value = None
        
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        mock_logger.info.assert_any_call(
            "request_completed",
            request_id=mock_request.state.request_id,
            status_code=200,
        )

    @pytest.mark.asyncio
    @patch("app.layer4_infrastructure.middleware.request_id.logger")
    async def test_logs_and_clears_on_exception(
        self, mock_logger, middleware, mock_request
    ):
        """Test that exceptions are logged with request context."""
        mock_request.headers.get.return_value = None
        
        async def call_next_with_error(request):
            raise ValueError("Something went wrong")
        
        with pytest.raises(ValueError):
            await middleware.dispatch(mock_request, call_next_with_error)
        
        # Should log error
        mock_logger.error.assert_called_once()
        error_call = mock_logger.error.call_args
        assert error_call[0][0] == "request_failed"
        assert "request_id" in error_call.kwargs

    @pytest.mark.asyncio
    async def test_request_id_format_is_uuid(self, middleware, mock_request, mock_call_next):
        """Test that generated request IDs are valid UUIDs."""
        mock_request.headers.get.return_value = None
        
        # Generate multiple request IDs
        request_ids = []
        for _ in range(5):
            mock_request.state = Mock()
            response = await middleware.dispatch(mock_request, mock_call_next)
            request_ids.append(response.headers["X-Request-ID"])
        
        # All should be valid and unique UUIDs
        for request_id in request_ids:
            try:
                uuid.UUID(request_id)
            except ValueError:
                assert False, f"Invalid UUID: {request_id}"
        
        # All should be unique
        assert len(set(request_ids)) == len(request_ids)
