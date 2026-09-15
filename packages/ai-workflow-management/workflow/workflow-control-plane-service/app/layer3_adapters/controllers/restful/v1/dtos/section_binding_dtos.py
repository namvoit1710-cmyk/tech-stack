"""Request DTOs for the SA-2605 section-only binding REST API."""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class SetWorkflowSectionRequest(BaseModel):
    """Bind a workflow to one section. Accepts ``section`` or ``sectionId`` from
    the FE. An empty value unbinds the workflow."""

    section: str = Field(
        default="",
        validation_alias=AliasChoices("section", "sectionId", "section_id"),
    )

    model_config = {"populate_by_name": True}


class SetSectionMembershipRequest(BaseModel):
    """Replace-set the environments that belong to a section. Accepts
    ``environment_ids`` (snake_case) or ``environmentIds`` (camelCase)."""

    environment_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("environment_ids", "environmentIds"),
    )

    model_config = {"populate_by_name": True}
