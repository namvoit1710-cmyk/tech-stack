from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.workflow_run_reader_interface import (
    IWorkflowRunReader,
)

_CLARIFY_QUESTION = (
    "Which workflow run should I look at? Give me the run_id, or the workflow name "
    "(or its id) and I'll find its latest failed run."
)


def run_reference_clarification(
    run_id: str = "", workflow_id: str = "", workflow_name: str = ""
) -> str | None:
    """Deterministic gate for AC A1: when the user reports a broken workflow but gives no
    run reference at all, return the question to ask; otherwise None (proceed to resolve)."""
    if run_id or workflow_id or workflow_name:
        return None
    return _CLARIFY_QUESTION


@dataclass
class ResolvedRun:
    """A failed run located for diagnosis: its id and its execution log."""

    run_id: str = ""
    execution_log: dict[str, Any] = field(default_factory=dict)

    def found(self) -> bool:
        return bool(self.run_id)


class ResolveFailedRun:
    """Turn a user's broken-workflow reference (run_id, workflow_id, or workflow_name)
    into a run id + execution log, via the read-only IWorkflowRunReader."""

    def __init__(self, reader: IWorkflowRunReader) -> None:
        self._reader = reader

    async def resolve(
        self, run_id: str = "", workflow_id: str = "", workflow_name: str = ""
    ) -> ResolvedRun:
        rid = run_id or await self._reader.resolve_run_id(
            workflow_id=workflow_id, workflow_name=workflow_name
        )
        if not rid:
            return ResolvedRun()
        log = await self._reader.get_execution_log(rid)
        return ResolvedRun(run_id=rid, execution_log=log)
