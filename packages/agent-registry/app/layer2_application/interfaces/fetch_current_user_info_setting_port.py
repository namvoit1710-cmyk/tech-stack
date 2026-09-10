"""Fetch workflow client settings port interface."""

from typing import Protocol

class FetchCurrentUserInfoClientSettings(Protocol):
    """Interface for fetching current user info client settings.
    
    This port defines the contract for accessing configuration settings related to the current user info API client.
    Infrastructure layer provides the implementation (e.g., loading from environment variables).
    """
    @property
    def fetch_current_user_info_url(self) -> str:
        """URL of the external Current User Info API."""
        ...
        
    @property
    def fetch_current_user_groups_url(self) -> str:
        """URL of the external Current User Groups API."""
        ...
        
    @property
    def fetch_current_user_groups_details_url(self) -> str:
        """URL of the external Current User Groups Details API."""
        ...

    @property
    def fetch_user_by_id_url(self) -> str:
        """URL template of the PM get-user-by-id API (placeholder: {external_id})."""
        ...
        
    @property
    def fetch_timeout_seconds(self) -> int:
        """Timeout for fetching current user info data from external API, in seconds."""
        ...