from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class DesignerRunResult:
    """Raw output of the inner tool-loop designer, BEFORE the ValidateRepairGate finalizes it.

    The designer only designs (discovers the palette, builds + structurally
    self-checks a candidate graph); it no longer decides the user-facing outcome.
    ``CANDIDATE`` carries the raw node/edge document for the gate to validate,
    persist, dry-run, repair and test-run.
    """

    CANDIDATE: ClassVar[str] = "candidate"
    NEEDS_CLARIFICATION: ClassVar[str] = "needs_clarification"
    ERROR: ClassVar[str] = "error"

    status: str = ERROR
    definition: dict = field(default_factory=dict)
    workflow_id: str = ""  # existing id to update, when refining a workflow already created
    source_workflow_id: str = ""  # source id to CLONE from, when creating a new workflow off a template
    remove_node_ids: list = field(default_factory=list)  # existing nodes to delete on update/clone
    content: str = ""      # the model's pre-gate narration (informational only)
    clarify_question: str = ""
    error_message: str = ""
