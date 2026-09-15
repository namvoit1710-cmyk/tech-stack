"""Bind a workflow to ONE section and a subset (≥1) of that section's
environments (SA-2450). Replace-set semantics: the Assign UI always sends the
full desired {section, environment_ids}.

Validation (422 WORKFLOW_ENVIRONMENT_INVALID):
* a section is required and at least one environment id is required (never 0) —
  enforced by the domain entity;
* every environment id must exist, be active, belong to this tenant, AND belong
  to the chosen section — the cross-aggregate rule enforced here (needs the
  Environment repo).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.workflow_environment_assignment import (
    WorkflowEnvironmentAssignment,
)
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)
from app.layer2_application.repositories.workflow_environment_repo_interface import (
    CASMismatch,
    IWorkflowEnvironmentRepo,
)


@dataclass
class SetWorkflowEnvironmentAssignmentCommand:
    tenant_id: str = "system"
    workflow_id: str = ""
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    updated_by: str = ""


@dataclass
class SetWorkflowEnvironmentAssignmentResult:
    workflow_id: str = ""
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> SetWorkflowEnvironmentAssignmentResult:
    return SetWorkflowEnvironmentAssignmentResult(
        error=message,
        error_code=ErrorCode.WORKFLOW_ENVIRONMENT_INVALID.code,
        error_details=details or {},
    )


class SetWorkflowEnvironmentAssignmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_environment_repo: IWorkflowEnvironmentRepo,
        environment_repo: IEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = workflow_environment_repo
        self.environment_repo = environment_repo

    async def execute(
        self, command: SetWorkflowEnvironmentAssignmentCommand
    ) -> SetWorkflowEnvironmentAssignmentResult:
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")

        section = (command.section or "").strip()
        env_ids = [e for e in (command.environment_ids or []) if (e or "").strip()]

        # Domain invariant: section required, ≥1 environment.
        try:
            WorkflowEnvironmentAssignment._validate(
                section=section, workflow_id=command.workflow_id, environment_ids=env_ids
            )
        except ValidationError as exc:
            return _invalid(str(exc))

        # Cross-aggregate rule: every id resolves to an active env of THIS tenant
        # AND that env is in the chosen section.
        for env_id in env_ids:
            env = self.environment_repo.get_by_id(env_id)
            if env is None or not env.is_active or env.tenant_id != command.tenant_id:
                return _invalid(
                    f"environment '{env_id}' does not exist for this tenant",
                    environment_id=env_id,
                )
            if (env.section or "") != section:
                return _invalid(
                    f"environment '{env.name}' is not in section '{section}'",
                    environment_id=env_id,
                    section=section,
                    env_section=env.section,
                )

        existing = self.repo.get_by_workflow(
            tenant_id=command.tenant_id, workflow_id=command.workflow_id
        )
        try:
            if existing is not None:
                stored_version = existing.version
                existing.set_binding(
                    section=section,
                    environment_ids=env_ids,
                    updated_by=command.updated_by,
                )
                self.repo.save(existing, expected_version=stored_version)
                entity = existing
            else:
                entity = WorkflowEnvironmentAssignment.create(
                    tenant_id=command.tenant_id,
                    workflow_id=command.workflow_id,
                    section=section,
                    environment_ids=env_ids,
                    created_by=command.updated_by,
                )
                self.repo.save(entity)
        except ValidationError as exc:
            return _invalid(str(exc))
        except CASMismatch as exc:
            return _invalid(str(exc))

        self.logger.info(
            "Workflow environment assignment saved",
            workflow_id=entity.workflow_id, section=entity.section,
            environment_ids=entity.environment_ids, tenant_id=entity.tenant_id,
        )
        return SetWorkflowEnvironmentAssignmentResult(
            workflow_id=entity.workflow_id,
            section=entity.section,
            environment_ids=list(entity.environment_ids),
            version=entity.version,
        )
