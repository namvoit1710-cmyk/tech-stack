"""Health check service port - interface for agent health checking.

This is a Protocol (interface) that defines health check operations.
Infrastructure layer will implement this using HTTP client.
"""

from typing import Protocol


class IHealthCheckClient(Protocol):
    """Interface for health check service operations.
    
    This port defines the contract for checking agent health.
    Infrastructure layer provides the implementation (e.g., using httpx).
    """

    async def check_health(self, endpoint: str) -> tuple[str, bool]:
        """Check if an agent is alive by pinging its health endpoint.
        
        Args:
            endpoint: Health check endpoint URL
            
        Returns:
            Tuple containing the endpoint and a boolean indicating if the agent is alive
        """
        ...
        
    async def batch_check_health(self, endpoints: list[str]) -> dict[str, bool]:
        """Batch check health of multiple agents.
        
        Args:
            endpoints: List of health check endpoint URLs
            
        Returns:
            Dictionary mapping endpoint URLs to their health status (True/False)
        """
        ...