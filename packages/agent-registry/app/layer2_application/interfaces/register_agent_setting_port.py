"""Setting port interface for the agent registry application."""

from typing import Protocol

class IRegisterAgentSettingPort(Protocol):
    """Interface for application settings.
    
    This port defines the contract for accessing application settings.
    Infrastructure layer provides the implementation (e.g., using Pydantic).
    """
        
    # SA-1938: no default_provider / default_model / default_temperature - an
    # omitted value stays unset (None) instead of being replaced by a default.

    @property
    def default_max_tokens(self) -> int:
        """Default max tokens for the agent."""
        ...
        
    @property
    def default_timeout_ms(self) -> int:
        """Default timeout in milliseconds for the agent."""
        ...
        
    @property
    def default_max_concurrency(self) -> int:
        """Default max concurrency for the agent."""
        ...
        
    @property
    def default_retry_count(self) -> int:
        """Default retry count for the agent."""
        ...
        
    @property
    def default_streaming_supported(self) -> bool:
        """Default streaming supported flag for the agent."""
        ...