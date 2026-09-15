from dataclasses import dataclass

from app.layer1_domain.entities.ai_usage import AiUsage


@dataclass
class AiCompletionResponse:
    """Response from an AI completion call."""
    content: str
    model: str
    usage: AiUsage
    finish_reason: str
