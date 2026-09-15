"""Reassign a workflow's owner (SA-2573).

Any authenticated caller may set the owner — access control is intentionally NOT
enforced here: permissions will be defined centrally on Keycloak (roles /
policies) later. The caller identity is still recorded as ``updated_by``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class SetWorkflowOwnerCommand:
    workflow_id: str = ""
    owner: str = ""          # the new owner's id (Keycloak ``sub``)
    actor_id: str = "system"  # the verified caller (from resolve_actor) — stamped as updated_by


@dataclass
class SetWorkflowOwnerResult:
    id: str = ""
    owner: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class SetWorkflowOwnerUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: SetWorkflowOwnerCommand) -> SetWorkflowOwnerResult:
        self.logger.info(
            "Reassigning workflow owner", id=command.workflow_id, owner=command.owner,
        )
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return SetWorkflowOwnerResult(
                error="workflow not found",
                error_code="NOT_FOUND",
                error_details={"workflow_id": command.workflow_id},
            )

        new_owner = (command.owner or "").strip()
        if not new_owner:
            return SetWorkflowOwnerResult(
                error="owner must not be empty",
                error_code="VALIDATION_ERROR",
                error_details={"field": "owner"},
            )

        # No access-control gate here — any authenticated caller may reassign the
        # owner. Permission enforcement is deferred to Keycloak (central identity).
        workflow.owner = new_owner
        workflow.updated_by = command.actor_id
        workflow.updated_at = datetime.now(timezone.utc).isoformat()
        await self.workflow_repo.asave(workflow)
        return SetWorkflowOwnerResult(id=workflow.id, owner=workflow.owner)
