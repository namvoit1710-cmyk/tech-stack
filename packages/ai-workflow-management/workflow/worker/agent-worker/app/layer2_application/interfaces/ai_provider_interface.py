from typing import Protocol

from app.layer1_domain.entities.ai_completion_request import AiCompletionRequest
from app.layer1_domain.entities.ai_completion_response import AiCompletionResponse


class IAiProvider(Protocol):
    """Interface for AI provider implementations."""

    def chat_completion(self, request: AiCompletionRequest) -> AiCompletionResponse:
        """Send a chat completion request to the AI provider and return the response."""
        ...
