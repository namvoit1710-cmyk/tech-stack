"""List every section's environment membership for a tenant (SA-2605).

Powers the Sections tab (which environments each section groups) and lets the FE
derive an environment's sections client-side (reverse view).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.section_membership_repo_interface import (
    ISectionMembershipRepo,
)


@dataclass
class ListSectionMembershipsCommand:
    tenant_id: str = "system"


@dataclass
class ListSectionMembershipsResult:
    memberships: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListSectionMembershipsUseCase:
    def __init__(
        self,
        logger: ILogger,
        section_membership_repo: ISectionMembershipRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = section_membership_repo

    async def execute(self, command: ListSectionMembershipsCommand) -> ListSectionMembershipsResult:
        rows = self.repo.list_all(tenant_id=command.tenant_id)
        return ListSectionMembershipsResult(
            memberships=[
                {"section": r.section, "environment_ids": list(r.environment_ids), "version": r.version}
                for r in rows
            ]
        )
