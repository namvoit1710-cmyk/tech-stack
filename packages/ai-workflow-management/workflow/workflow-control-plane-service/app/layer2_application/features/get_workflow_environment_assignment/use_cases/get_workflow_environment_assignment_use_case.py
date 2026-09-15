"""Read a workflow's environment assignment (SA-2450).

Returns ``{workflow_id, section, environment_ids}``. An unassigned workflow is
NOT an error — it returns an empty section and empty list, which the FE renders
as an empty (hard-scoped) selector (SA-2450 W7).

SA-2481: the assignment stores the section by NAME, which is mutable — renaming a
section in Config (which rewrites ``AIW_ENVIRONMENT.SECTION`` on the envs) would
otherwise leave the stored ``AIW_WORKFLOW_ENVIRONMENT.SECTION`` stale, so the
Assign tab prefilled a section that no longer exists. To make the section
reference survive renames, the section is **derived at read time** from the
assigned environments' *current* section (env ids are the stable reference); the
stored name is only a fallback when no assigned env still resolves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)
from app.layer2_application.repositories.workflow_environment_repo_interface import (
    IWorkflowEnvironmentRepo,
)


@dataclass
class GetWorkflowEnvironmentAssignmentCommand:
    tenant_id: str = "system"
    workflow_id: str = ""


@dataclass
class GetWorkflowEnvironmentAssignmentResult:
    workflow_id: str = ""
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    assigned: bool = False
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetWorkflowEnvironmentAssignmentUseCase:
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
        self, command: GetWorkflowEnvironmentAssignmentCommand
    ) -> GetWorkflowEnvironmentAssignmentResult:
        entity = self.repo.get_by_workflow(
            tenant_id=command.tenant_id, workflow_id=command.workflow_id
        )
        if entity is None:
            return GetWorkflowEnvironmentAssignmentResult(
                workflow_id=command.workflow_id,
                section="",
                environment_ids=[],
                assigned=False,
                version=0,
            )

        # SA-2481: derive the section from the assigned envs' CURRENT section so a
        # Config-side rename doesn't leave a stale name. Keep only ids that still
        # resolve to an active env of this tenant; adopt their section (they share
        # one by invariant). Fall back to the stored name only if none resolve.
        section = entity.section
        valid_ids: list[str] = []
        for env_id in entity.environment_ids:
            try:
                env = self.environment_repo.get_by_id(env_id)
            except Exception:
                env = None
            if env is None or not env.is_active or env.tenant_id != command.tenant_id:
                continue
            valid_ids.append(env_id)
            section = env.section or section  # current name wins

        return GetWorkflowEnvironmentAssignmentResult(
            workflow_id=entity.workflow_id,
            section=section,
            environment_ids=valid_ids or list(entity.environment_ids),
            assigned=True,
            version=entity.version,
        )
