"""Unit tests for HTTPX health check client."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.layer4_infrastructure.external_tools.httpx_health_check_client import (
    HTTPXHealthCheckService,
)
from app.layer4_infrastructure.external_tools.httpx_retry import HttpxRetryHelper


class TestHTTPXHealthCheckService:
    """Test retry and timeout behavior for the health check client."""

    @pytest.mark.asyncio
    async def test_retry_wrapper_uses_settings_retry_count(self):
        """Retry wrapper should respect retry count from settings."""
        retry_count = 3
        attempts = 0

        async def flaky_ping(endpoint: str, timeout_seconds: int = 5) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts < retry_count:
                raise httpx.ConnectError("connection failed")
            return True

        wrapped_ping = HttpxRetryHelper.wrap_with_fixed_retry(
            flaky_ping,
            retry_count=retry_count,
            wait_seconds=0,
            retry_predicate=lambda e: isinstance(e, (httpx.ConnectError, httpx.TimeoutException)),
        )

        result = await wrapped_ping("http://localhost:8000/health", 5)

        assert result is True
        assert attempts == retry_count

    @pytest.mark.asyncio
    async def test_check_health_uses_timeout_from_settings(self):
        """check_health should forward configured timeout to ping_agent."""
        settings = Mock(
            health_check_timeout_seconds=7,
            health_check_retry_count=2,
            health_check_backoff_seconds=0,
        )
        service = HTTPXHealthCheckService(settings)
        service._ping_agent_with_retry = AsyncMock(return_value=True)

        result = await service.check_health("http://localhost:8000/health")

        assert result == ("http://localhost:8000/health", True)
        service._ping_agent_with_retry.assert_awaited_once_with(
            "http://localhost:8000/health",
            settings.health_check_timeout_seconds,
        )
