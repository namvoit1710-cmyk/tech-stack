from dataclasses import dataclass

from app.layer1_domain.entities.ai_message import AiMessage
from app.layer1_domain.value_objects.response_format import ResponseFormat


@dataclass
class AiCompletionRequest:
    """Request payload for an AI completion call."""
    messages: list[AiMessage]
    model: str
    temperature: float = 0.7
    max_tokens: int = 1024
    top_p: float = 1.0
    response_format: ResponseFormat = ResponseFormat.TEXT
