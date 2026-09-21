"""Unit tests for rate limit middleware."""

import time
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import Request, HTTPException
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from app.layer4_infrastructure.middleware.rate_limit import RateLimitMiddleware


class TestRateLimitMiddleware:
    """Test rate limit middleware."""

    @pytest.fixture
    def middleware(self):
        """Create middleware instance with low limits for testing."""
        app = Mock()
        return RateLimitMiddleware(app, max_requests=3, window_seconds=60)

    @pytest.fixture
    def mock_request(self):
        """Create mock request."""
        request = Mock(spec=Request)
        request.url.path = "/api/test"
        request.headers.get.return_value = None
        request.client = Mock()
        request.client.host = "127.0.0.1"
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create mock call_next function."""
        async def call_next(request):
            response = Mock()
            response.headers = {}
            return response
        return call_next

    def test_get_client_ip_from_direct_connection(self, middleware, mock_request):
        """Test extracting IP from direct connection."""
        mock_request.client.host = "192.168.1.100"
        
        ip = middleware._get_client_ip(mock_request)
        
        assert ip == "192.168.1.100"

    def test_get_client_ip_from_forwarded_header(self, middleware, mock_request):
        """Test extracting IP from X-Forwarded-For header."""
        mock_request.headers.get.return_value = "203.0.113.1, 198.51.100.1"
        
        ip = middleware._get_client_ip(mock_request)
        
        assert ip == "203.0.113.1"

    def test_get_client_ip_handles_missing_client(self, middleware):
        """Test handling request without client info."""
        request = Mock(spec=Request)
        request.headers.get.return_value = None
        request.client = None
        
        ip = middleware._get_client_ip(request)
        
        assert ip == "unknown"

    def test_cleanup_old_requests(self, middleware):
        """Test cleanup of old requests outside time window."""
        current_time = time.time()
        middleware.request_counts["127.0.0.1"] = [
            current_time - 100,  # Outside window (60s)
            current_time - 30,   # Inside window
            current_time - 10,   # Inside window
        ]
        
        middleware._cleanup_old_requests("127.0.0.1", current_time)
        
        assert len(middleware.request_counts["127.0.0.1"]) == 2

    @pytest.mark.asyncio
    async def test_allows_requests_within_limit(self, middleware, mock_request, mock_call_next):
        """Test that requests within rate limit are allowed."""
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert response is not None
        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "3"
        assert response.headers["X-RateLimit-Remaining"] == "2"

    @pytest.mark.asyncio
    async def test_blocks_requests_exceeding_limit(self, middleware, mock_request, mock_call_next):
        """Test that requests exceeding rate limit are blocked."""
        # Make 3 requests (at limit)
        await middleware.dispatch(mock_request, mock_call_next)
        await middleware.dispatch(mock_request, mock_call_next)
        await middleware.dispatch(mock_request, mock_call_next)
        
        # 4th request should be blocked
        with pytest.raises(HTTPException) as exc_info:
            await middleware.dispatch(mock_request, mock_call_next)
        
        assert exc_info.value.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert "Rate limit exceeded" in exc_info.value.detail
        assert "Retry-After" in exc_info.value.headers

    @pytest.mark.asyncio
    async def test_skips_health_check_endpoint(self, middleware, mock_call_next):
        """Test that health check endpoint bypasses rate limiting."""
        health_request = Mock(spec=Request)
        health_request.url.path = "/health"
        health_request.client = Mock()
        health_request.client.host = "127.0.0.1"
        
        # Make many requests to health endpoint
        for _ in range(10):
            response = await middleware.dispatch(health_request, mock_call_next)
            assert response is not None

    @pytest.mark.asyncio
    async def test_tracks_requests_per_client_separately(self, middleware, mock_call_next):
        """Test that different clients have separate rate limits."""
        request1 = Mock(spec=Request)
        request1.url.path = "/api/test"
        request1.headers.get.return_value = None
        request1.client = Mock()
        request1.client.host = "192.168.1.1"
        
        request2 = Mock(spec=Request)
        request2.url.path = "/api/test"
        request2.headers.get.return_value = None
        request2.client = Mock()
        request2.client.host = "192.168.1.2"
        
        # Each client can make 3 requests
        await middleware.dispatch(request1, mock_call_next)
        await middleware.dispatch(request1, mock_call_next)
        await middleware.dispatch(request1, mock_call_next)
        
        await middleware.dispatch(request2, mock_call_next)
        await middleware.dispatch(request2, mock_call_next)
        
        # request1 is at limit
        with pytest.raises(HTTPException):
            await middleware.dispatch(request1, mock_call_next)
        
        # request2 can still make one more
        response = await middleware.dispatch(request2, mock_call_next)
        assert response is not None

    @pytest.mark.asyncio
    async def test_rate_limit_headers_in_response(self, middleware, mock_request, mock_call_next):
        """Test that rate limit headers are added to response."""
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        assert int(response.headers["X-RateLimit-Limit"]) == 3
