from typing import Any, Protocol


class IWorkflowClient(Protocol):
    async def get_execution_log(
        self, run_id: str, offset: int = 0, include_children: bool = True
    ) -> dict[str, Any]:
        """Fetch a run's execution log from the Workflow Control Plane
        (`GET /api/v1/runs/{run_id}/execution-log`)."""
        ...

    async def resolve_run_id(self, workflow_id: str = "", workflow_name: str = "") -> str:
        """Resolve a run to diagnose from a workflow id or name (prefers the latest failed run)."""
        ...
