from __future__ import annotations

from typing import Any, Protocol


class IWorkflowRunReader(Protocol):
    """Read-only access to a Workflow Control Plane run, for diagnose-and-repair.

    Mirrors the troubleshooting-agent's IWorkflowClient contract (same control-plane
    endpoints) so a failed run can be resolved and its execution log fetched from the
    designer side. Read-only: never mutates a workflow or a run.
    """

    async def resolve_run_id(self, workflow_id: str = "", workflow_name: str = "") -> str:
        """Resolve a run to diagnose from a workflow id or name (prefers the latest failed run)."""
        ...

    async def get_execution_log(
        self, run_id: str, offset: int = 0, include_children: bool = True
    ) -> dict[str, Any]:
        """Fetch a run's execution log (`GET /api/v1/runs/{run_id}/execution-log`)."""
        ...
