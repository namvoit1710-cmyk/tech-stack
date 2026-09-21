from __future__ import annotations

from typing import Protocol


class WorkflowRepairError(RuntimeError):
    """Raised when a one-shot repair attempt cannot produce a corrected definition."""


class IWorkflowRepairer(Protocol):
    """Port for a one-shot LLM fix of a workflow definition that failed validation.

    Returns a corrected definition PAYLOAD (the same document shape the designer
    produces: name/description/nodes/edges) — it never edits workflow code and never
    touches the runtime execution engine. The gate re-persists the correction itself.
    """

    async def repair(self, definition: dict, error_message: str, error_details: dict) -> dict: ...
