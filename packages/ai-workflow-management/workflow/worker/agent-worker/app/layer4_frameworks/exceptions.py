class AgentWorkerError(Exception):
    """Base exception for agent worker."""


class AiProviderConnectionError(AgentWorkerError):
    """Raised when a connection to the AI provider cannot be established."""


class AiProviderAuthenticationError(AgentWorkerError):
    """Raised when authentication with the AI provider fails."""


class AiProviderRateLimitError(AgentWorkerError):
    """Raised when the AI provider rate limit is exceeded."""


class AiProviderResponseError(AgentWorkerError):
    """Raised when the AI provider returns an unexpected response."""
