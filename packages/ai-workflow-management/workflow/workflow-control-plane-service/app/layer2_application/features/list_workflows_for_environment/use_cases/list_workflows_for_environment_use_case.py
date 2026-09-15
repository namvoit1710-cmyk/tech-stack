"""Reverse lookup: which workflows reference a given environment (SA-2450 W11).

Used for the SA-2365 delete-confirm ("used by N workflows") and overview.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_environment_repo_interface import (
    IWorkflowEnvironmentRepo,
)


@dataclass
class ListWorkflowsForEnvironmentCommand:
    tenant_id: str = "system"
    environment_id: str = ""


@dataclass
class ListWorkflowsForEnvironmentResult:
    environment_id: str = ""
    workflow_ids: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    total: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListWorkflowsForEnvironmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_environment_repo: IWorkflowEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = workflow_environment_repo

    async def execute(
        self, command: ListWorkflowsForEnvironmentCommand
    ) -> ListWorkflowsForEnvironmentResult:
        assignments = self.repo.list_by_environment(
            tenant_id=command.tenant_id, environment_id=command.environment_id
        )
        items = [
            {"workflow_id": a.workflow_id, "section": a.section}
            for a in assignments
        ]
        return ListWorkflowsForEnvironmentResult(
            environment_id=command.environment_id,
            workflow_ids=[a.workflow_id for a in assignments],
            items=items,
            total=len(items),
        )
