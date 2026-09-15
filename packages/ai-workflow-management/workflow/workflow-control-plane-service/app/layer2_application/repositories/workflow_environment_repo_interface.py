from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.workflow_environment_assignment import (
    WorkflowEnvironmentAssignment,
)
# CASMismatch is shared across repos; reuse the gateway one so update use cases
# map a single exception type to HTTP 409.
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch  # noqa: F401


class IWorkflowEnvironmentRepo(Protocol):
    """Persistence for the workflow↔section binding (SA-2450).

    One ACTIVE assignment per (tenant_id, workflow_id): a workflow binds to
    exactly one section with a subset (≥1) of that section's environments.
    """

    def save(
        self,
        entity: WorkflowEnvironmentAssignment,
        *,
        expected_version: int | None = None,
    ) -> None:
        """Upsert. When ``expected_version`` is set, raise CASMismatch on mismatch."""

    def get_by_workflow(
        self, *, tenant_id: str, workflow_id: str
    ) -> WorkflowEnvironmentAssignment | None:
        """The single ACTIVE assignment for a workflow, or None (unassigned)."""

    def list_by_environment(
        self, *, tenant_id: str, environment_id: str
    ) -> list[WorkflowEnvironmentAssignment]:
        """Reverse lookup: every ACTIVE assignment that references this env id."""

    def soft_delete_by_workflow(self, *, tenant_id: str, workflow_id: str) -> None:
        """Deactivate a workflow's assignment (e.g. workflow deleted)."""
