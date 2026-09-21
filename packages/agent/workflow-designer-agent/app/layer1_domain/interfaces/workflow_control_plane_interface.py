from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.workflow_run import DryRunOutcome, RunOutcome, WorkflowRef


class WorkflowControlPlaneError(RuntimeError):
    """Raised when the workflow control plane rejects a request or is unreachable."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")


class IWorkflowControlPlane(Protocol):
    """Port for loading, persisting, dry-running and test-running a workflow definition."""

    async def get(self, workflow_id: str) -> dict: ...
    """Load an existing workflow by id, returned in the agent's definition shape
    ({name, description, nodes:[{id,type,name,config}], edges:[{source,target}]})."""

    async def list(self) -> list[dict]: ...
    """List existing workflows as lightweight summaries ({id, name, description}) so a
    named workflow can be resolved to its id (e.g. to clone/update it by name)."""

    async def create(self, definition: dict) -> WorkflowRef: ...

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef: ...

    async def patch(self, workflow_id: str, definition: dict) -> WorkflowRef: ...
    """Partial-persist an existing workflow (PATCH) keeping its id stable — used for a
    diff update where the full merged graph is sent but no new version is forked."""

    async def dry_run(self, workflow_id: str, input_data: dict | None = None) -> DryRunOutcome: ...

    async def test_run(
        self, workflow_id: str, input_data: dict, skip_validation: bool = True
    ) -> RunOutcome: ...
