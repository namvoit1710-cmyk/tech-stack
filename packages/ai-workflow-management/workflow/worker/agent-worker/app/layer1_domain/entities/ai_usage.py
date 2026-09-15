from dataclasses import dataclass


@dataclass
class AiUsage:
    """Token usage statistics from an AI completion."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
