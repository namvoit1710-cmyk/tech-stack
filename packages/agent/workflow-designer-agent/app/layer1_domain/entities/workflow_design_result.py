from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class WorkflowDesignResult:
    """Outcome of the design flow: a designed+tested workflow, a clarifying question, or an error."""

    SUCCESS: ClassVar[str] = "success"
    NEEDS_CLARIFICATION: ClassVar[str] = "needs_clarification"
    ERROR: ClassVar[str] = "error"

    status: str = ERROR
    content: str = ""
    workflow: dict = field(default_factory=dict)
    workflow_id: str = ""
    run_id: str = ""
    link: str = ""
    raw: dict = field(default_factory=dict)
