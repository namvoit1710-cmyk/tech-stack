from enum import Enum


class AiRole(str, Enum):
    """Supported message roles in AI conversations."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
