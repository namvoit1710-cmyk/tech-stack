"""HTTPX Health Check Service - Implementation of IHealthCheckService port."""

import asyncio

import httpx

from app.layer2_application.interfaces.health_check_client_port import (
    IHealthCheckClient,
)
from app.layer4_infrastructure.logger.app_logger import get_logger
from app.layer4_infrastructure.external_tools.httpx_retry import HttpxRetryHelper
from app.layer2_application.interfaces.health_check_client_setting_port import HealthCheckClientSettings


logger = get_logger(__name__)


class HTTPXHealthCheckService(IHealthCheckClient):
    """HTTPX implementation of Health Check Service.
    
    Uses httpx.AsyncClient to ping agent health endpoints with retry logic.
    """

    def __init__(self, settings: HealthCheckClientSettings):
        """Initialize health check service.
        
        Args:
            settings: Application settings containing health check configuration
        """
        self.settings = settings
        self._ping_agent_with_retry = HttpxRetryHelper.wrap_with_fixed_retry(
            self._ping_agent_once,
            retry_count=settings.health_check_retry_count,
            wait_seconds=settings.health_check_backoff_seconds,
            retry_predicate=lambda e: isinstance(e, (httpx.ConnectError, httpx.TimeoutException)),
        )

    async def check_health(self, endpoint: str) -> tuple[str, bool]:
        """Check if an agent is alive by pinging its health endpoint.
        
        Args:
            endpoint: Health check endpoint URL
            
        Returns:
            Tuple containing the endpoint and a boolean indicating if the agent is alive
        """
        try:
            is_alive = await self._ping_agent_with_retry(endpoint, self.settings.health_check_timeout_seconds)
            return (endpoint, is_alive)
        except Exception as e:
            # All exceptions will be logged and treated as unhealthy for this endpoint
            logger.error(
                "health_check_unexpected_error",
                endpoint=endpoint,
                error=str(e),
                error_type=type(e).__name__,
            )
            return (endpoint, False)

    async def _ping_agent_once(self, endpoint: str, timeout_seconds: int = 5) -> bool:
        """Ping an agent's endpoint with custom timeout and retry logic.
        
        Args:
            endpoint: Endpoint URL to ping
            timeout_seconds: Timeout in seconds
            
        Returns:
            True if agent responds within timeout with 2xx status, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(endpoint)
                is_healthy = 200 <= response.status_code < 300
                
                if is_healthy:
                    logger.debug(
                        "health_check_success",
                        endpoint=endpoint,
                        status_code=response.status_code,
                    )
                else:
                    logger.warning(
                        "health_check_unhealthy",
                        endpoint=endpoint,
                        status_code=response.status_code,
                    )
                
                return is_healthy
                
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(
                "health_check_connection_error",
                endpoint=endpoint,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise  # Let retry decorator handle it
        except Exception as e:
            logger.error(
                "health_check_unexpected_error",
                endpoint=endpoint,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def batch_check_health(self, endpoints: list[str]) -> dict[str, bool]:
        """Check health of multiple agents in parallel.
        
        Args:
            endpoints: List of health check endpoint URLs
            
        Returns:
            Dictionary mapping endpoint to health status (True/False)
        """

        # Execute all health checks in parallel
        # Exception is handled within check_single_endpoint, so we can gather all results without try/except here
        results = await asyncio.gather(
            *[self.check_health(endpoint) for endpoint in endpoints]
        )

        # Build result dictionary
        health_statuses = {}
        for result in results:
            if isinstance(result, tuple):
                endpoint, is_alive = result
                health_statuses[endpoint] = is_alive

        return health_statuses
