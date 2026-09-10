"""Fetch workflow client settings port interface."""

from typing import Protocol

class FetchWorkflowClientSettings(Protocol):
    """Interface for fetching workflow client settings.
    
    This port defines the contract for accessing configuration settings related to the workflow API client.
    Infrastructure layer provides the implementation (e.g., loading from environment variables).
    """
    @property
    def fetch_workflow_url(self) -> str:
        """URL of the external Workflow Control Plane API."""
        ...
        
    @property
    def fetch_timeout_seconds(self) -> int:
        """Timeout for fetching workflow data from external API, in seconds."""
        ...
        
    @property
    def fetch_retry_count(self) -> int:
        """Total attempts for fetching workflow data (retries = n-1). 1 = no retry."""
        ...
        
    @property
    def fetch_backoff_seconds(self) -> int:
        """Backoff time between retry attempts for fetching workflow data from external API, in seconds."""
        ...
        
    @property
    def retryable_exceptions(self) -> list[int]:
        """List of HTTP status codes that should trigger a retry when fetching workflow data from external API."""
        ...
        
    @property
    def fetch_min_backoff_seconds(self) -> int:
        """Minimum backoff time between retry attempts for fetching workflow data from external API, in seconds."""
        ...
        
    @property
    def fetch_max_backoff_seconds(self) -> int:
        """Maximum backoff time between retry attempts for fetching workflow data from external API, in seconds."""
        ...