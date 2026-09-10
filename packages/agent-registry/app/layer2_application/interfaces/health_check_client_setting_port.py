"""Health check client settings interface for fetching external API configuration.
This interface defines the contract for accessing configuration settings related to health check operations."""

from typing import Protocol

class HealthCheckClientSettings(Protocol):
    """Interface for fetching health check client settings.
    
    This port defines the contract for accessing configuration settings related to the health check API client.
    Infrastructure layer provides the implementation (e.g., loading from environment variables).
    """
    @property
    def health_check_retry_count(self) -> int:
        """Total attempts for health check (retries = n-1). 1 = no retry."""
        ...
        
    @property
    def health_check_backoff_seconds(self) -> int:
        """Backoff time between retry attempts for health check API client, in seconds."""
        ...
        
        
    @property
    def health_check_timeout_seconds(self) -> int:
        """Timeout for health check API client, in seconds."""
        ...