"""List environments for a tenant (optionally filtered by section/name)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
    ListEnvironmentsFilter,
)


@dataclass
class ListEnvironmentsCommand:
    tenant_id: str = "system"
    section: str = ""
    name_contains: str = ""
    include_inactive: bool = False
    skip: int = 0
    top: int = 100


@dataclass
class ListEnvironmentsResult:
    items: list[dict] = field(default_factory=list)
    total: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListEnvironmentsUseCase:
    def __init__(
        self,
        logger: ILogger,
        environment_repo: IEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = environment_repo

    async def execute(self, command: ListEnvironmentsCommand) -> ListEnvironmentsResult:
        page = self.repo.list_paged(
            ListEnvironmentsFilter(
                tenant_id=command.tenant_id,
                section=command.section,
                name_contains=command.name_contains,
                include_inactive=command.include_inactive,
                skip=command.skip,
                top=command.top,
            )
        )
        return ListEnvironmentsResult(
            items=[e.to_public_dict() for e in page.items],
            total=page.total,
        )
