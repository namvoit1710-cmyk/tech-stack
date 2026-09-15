from enum import Enum


class AiProvider(str, Enum):
    """Supported AI providers."""
    OPENAI = "openai"
