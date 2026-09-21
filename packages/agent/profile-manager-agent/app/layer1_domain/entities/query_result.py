from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class QueryResult:
    """The outcome of executing an OData query against profile-management.

    ``odata_query`` holds the encoded query string that was sent to the service
    (kept for echoing back into the evaluation, exactly as the old dict did).
    """

    users: List[Any] = field(default_factory=list)
    odata_query: str = ""
    user_prompt: str = ""
    error: Optional[str] = None


@dataclass
class QueryEvaluation:
    """The verdict on a ``QueryResult`` — mirrors the old ``agent_data`` payload."""

    valid: str  # "correct" | "warning" | "incorrect"
    message: str
    item_size: str
    expression: str
    user_prompt: str
