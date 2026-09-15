"""Bind a workflow to ONE section (SA-2605).

Replace semantics: the Workflow-binding UI sends the desired ``section``. An empty
section unbinds the workflow (unassigned). There is no per-flow environment subset
any more — at run time the flow may use any environment of the bound section
(membership lives in AIW_SECTION_ENVIRONMENT), and the run guard enforces it.

Validation (422 WORKFLOW_ENVIRONMENT_INVALID): a section is required to bind. If a
section schema repo is available, the section must be a known section (has a
schema) so a workflow can't bind to a typo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.workflow_section import WorkflowSection
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_section_repo_interface import (
    CASMismatch,
    IWorkflowSectionRepo,
)


@dataclass
class SetWorkflowSectionCommand:
    tenant_id: str = "system"
    workflow_id: str = ""
    section: str = ""
    updated_by: str = ""


@dataclass
class SetWorkflowSectionResult:
    workflow_id: str = ""
    section: str = ""
    assigned: bool = False
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> SetWorkflowSectionResult:
    return SetWorkflowSectionResult(
        error=message,
        error_code=ErrorCode.WORKFLOW_ENVIRONMENT_INVALID.code,
        error_details=details or {},
    )


class SetWorkflowSectionUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_section_repo: IWorkflowSectionRepo,
        env_section_schema_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = workflow_section_repo
        self.section_schema_repo = env_section_schema_repo

    async def execute(self, command: SetWorkflowSectionCommand) -> SetWorkflowSectionResult:
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")
        if not (command.workflow_id or "").strip():
            return _invalid("workflow_id is required")

        section = (command.section or "").strip()

        # Empty section → unbind the workflow (unassigned).
        if not section:
            self.repo.soft_delete_by_workflow(
                tenant_id=command.tenant_id, workflow_id=command.workflow_id
            )
            return SetWorkflowSectionResult(
                workflow_id=command.workflow_id, section="", assigned=False, version=0
            )

        # Section must be a known section (has a schema) when the schema repo is wired.
        if self.section_schema_repo is not None:
            try:
                known = self.section_schema_repo.get_by_natural_key(
                    tenant_id=command.tenant_id, section=section
                )
            except Exception:
                known = None
            if known is None:
                return _invalid(
                    f"section '{section}' does not exist", section=section
                )

        existing = self.repo.get_by_workflow(
            tenant_id=command.tenant_id, workflow_id=command.workflow_id
        )
        try:
            if existing is not None:
                stored_version = existing.version
                existing.set_section(section=section, updated_by=command.updated_by)
                self.repo.save(existing, expected_version=stored_version)
                entity = existing
            else:
                entity = WorkflowSection.create(
                    tenant_id=command.tenant_id,
                    workflow_id=command.workflow_id,
                    section=section,
                    created_by=command.updated_by,
                )
                self.repo.save(entity)
        except ValidationError as exc:
            return _invalid(str(exc))
        except CASMismatch as exc:
            return _invalid(str(exc))

        self.logger.info(
            "Workflow section binding saved",
            workflow_id=entity.workflow_id, section=entity.section,
            tenant_id=entity.tenant_id,
        )
        return SetWorkflowSectionResult(
            workflow_id=entity.workflow_id,
            section=entity.section,
            assigned=True,
            version=entity.version,
        )
