"""Read a workflow's section binding (SA-2605).

Returns ``{workflow_id, section, assigned}``. An unassigned workflow is NOT an
error — it returns an empty section, which the FE renders as an empty run-time
env picker (bind a section first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_section_repo_interface import (
    IWorkflowSectionRepo,
)


@dataclass
class GetWorkflowSectionCommand:
    tenant_id: str = "system"
    workflow_id: str = ""


@dataclass
class GetWorkflowSectionResult:
    workflow_id: str = ""
    section: str = ""
    assigned: bool = False
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetWorkflowSectionUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_section_repo: IWorkflowSectionRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = workflow_section_repo

    async def execute(self, command: GetWorkflowSectionCommand) -> GetWorkflowSectionResult:
        entity = self.repo.get_by_workflow(
            tenant_id=command.tenant_id, workflow_id=command.workflow_id
        )
        if entity is None:
            return GetWorkflowSectionResult(
                workflow_id=command.workflow_id, section="", assigned=False, version=0
            )
        return GetWorkflowSectionResult(
            workflow_id=entity.workflow_id,
            section=entity.section,
            assigned=True,
            version=entity.version,
        )
