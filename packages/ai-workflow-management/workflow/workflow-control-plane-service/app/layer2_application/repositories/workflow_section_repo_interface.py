from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.workflow_section import WorkflowSection
# CASMismatch is shared across repos; reuse the gateway one so update use cases
# map a single exception type to HTTP 409.
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch  # noqa: F401


class IWorkflowSectionRepo(Protocol):
    """Persistence for the workflow→section binding (SA-2605).

    One ACTIVE binding per (tenant_id, workflow_id): a workflow binds to exactly
    one section (no per-flow environment subset — that lived in SA-2450's
    AIW_WORKFLOW_ENVIRONMENT, now superseded).
    """

    def save(
        self,
        entity: WorkflowSection,
        *,
        expected_version: int | None = None,
    ) -> None:
        """Upsert. When ``expected_version`` is set, raise CASMismatch on mismatch."""

    def get_by_workflow(
        self, *, tenant_id: str, workflow_id: str
    ) -> WorkflowSection | None:
        """The single ACTIVE binding for a workflow, or None (unassigned)."""

    def list_by_section(
        self, *, tenant_id: str, section: str
    ) -> list[WorkflowSection]:
        """Every ACTIVE workflow bound to this section (for the Sections tab)."""

    def soft_delete_by_workflow(self, *, tenant_id: str, workflow_id: str) -> None:
        """Deactivate a workflow's binding (e.g. workflow deleted)."""

    def clear_section(self, *, tenant_id: str, section: str, updated_by: str) -> int:
        """Unbind every workflow of a section (e.g. the section is deleted).

        Returns the number of workflows unassigned.
        """
