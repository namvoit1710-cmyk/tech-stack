from enum import Enum


class AuthType(str, Enum):
    """Supported authentication types for HTTP requests."""
    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"
    API_KEY = "api_key"
