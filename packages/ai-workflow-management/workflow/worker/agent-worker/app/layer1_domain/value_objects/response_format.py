from enum import Enum


class ResponseFormat(str, Enum):
    """Supported AI response formats."""
    TEXT = "text"
    JSON = "json"
