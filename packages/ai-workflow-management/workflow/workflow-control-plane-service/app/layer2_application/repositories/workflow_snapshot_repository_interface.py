"""Workflow-snapshot repository protocol.

Persists the immutable workflow definition each ``WorkflowRun`` was
started against. The engine reads through this repo (via
``OrchestrationEngine._load_workflow_for_run``) instead of the live
``workflow_repo``, so mid-run edits to the workflow cannot leak into
in-flight runs.
"""

from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot


class IWorkflowSnapshotRepository(Protocol):
    def save(self, snapshot: WorkflowSnapshot) -> None: ...

    def get_by_id(self, snapshot_id: str) -> WorkflowSnapshot | None: ...

    def delete(self, snapshot_id: str) -> None: ...
