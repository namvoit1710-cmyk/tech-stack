"""Read a section's environment membership (SA-2605).

Returns ``{section, environment_ids}`` — the environments that belong to the
section (many-to-many). Empty membership is not an error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.section_membership_repo_interface import (
    ISectionMembershipRepo,
)


@dataclass
class GetSectionMembershipCommand:
    tenant_id: str = "system"
    section: str = ""


@dataclass
class GetSectionMembershipResult:
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetSectionMembershipUseCase:
    def __init__(
        self,
        logger: ILogger,
        section_membership_repo: ISectionMembershipRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = section_membership_repo

    async def execute(self, command: GetSectionMembershipCommand) -> GetSectionMembershipResult:
        entity = self.repo.get_by_section(
            tenant_id=command.tenant_id, section=command.section
        )
        if entity is None:
            return GetSectionMembershipResult(
                section=command.section, environment_ids=[], version=0
            )
        return GetSectionMembershipResult(
            section=entity.section,
            environment_ids=list(entity.environment_ids),
            version=entity.version,
        )
