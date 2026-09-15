"""Request DTOs for the workflow↔section binding REST API (SA-2450)."""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class SetWorkflowEnvironmentAssignmentRequest(BaseModel):
    """Replace-set the binding for a workflow. Accepts either ``environment_ids``
    (snake_case) or ``environmentIds`` (camelCase) from the FE."""

    section: str = ""
    environment_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("environment_ids", "environmentIds"),
    )

    model_config = {"populate_by_name": True}
