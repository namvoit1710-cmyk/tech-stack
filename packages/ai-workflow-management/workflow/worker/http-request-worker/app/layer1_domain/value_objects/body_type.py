from enum import Enum


class BodyType(str, Enum):
    """Supported body types for HTTP requests."""
    NONE = "none"
    RAW_JSON = "raw_json"
    RAW_TEXT = "raw_text"
    STRUCTURED = "structured"
    FORM_URLENCODED = "form_urlencoded"
    FILE = "file"
