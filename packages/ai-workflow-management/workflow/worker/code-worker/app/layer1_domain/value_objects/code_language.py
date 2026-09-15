from enum import Enum


class CodeLanguage(str, Enum):
    """Supported scripting languages for the Code node."""
    JAVASCRIPT = "javascript"
    PYTHON = "python"
