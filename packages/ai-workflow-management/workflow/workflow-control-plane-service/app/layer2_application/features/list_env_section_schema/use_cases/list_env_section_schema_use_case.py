"""List section schemas for a tenant (optionally filtered by section), each with
its environment_count (active env count in that section)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    IEnvSectionSchemaRepo,
    ListEnvSectionSchemasFilter,
)
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
    ListEnvironmentsFilter,
)


@dataclass
class ListEnvSectionSchemaCommand:
    tenant_id: str = "system"
    section: str = ""
    include_inactive: bool = False
    skip: int = 0
    top: int = 100


@dataclass
class ListEnvSectionSchemaResult:
    items: list[dict] = field(default_factory=list)
    total: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListEnvSectionSchemaUseCase:
    def __init__(
        self,
        logger: ILogger,
        env_section_schema_repo: IEnvSectionSchemaRepo,
        environment_repo: IEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = env_section_schema_repo
        self.env_repo = environment_repo

    async def execute(
        self, command: ListEnvSectionSchemaCommand
    ) -> ListEnvSectionSchemaResult:
        page = self.repo.list_paged(
            ListEnvSectionSchemasFilter(
                tenant_id=command.tenant_id,
                section=command.section,
                include_inactive=command.include_inactive,
                skip=command.skip,
                top=command.top,
            )
        )
        items: list[dict] = []
        for schema in page.items:
            count = self.env_repo.list_paged(
                ListEnvironmentsFilter(
                    tenant_id=schema.tenant_id, section=schema.section,
                    include_inactive=False, skip=0, top=1,
                )
            ).total
            data = schema.to_public_dict()
            data["environment_count"] = count
            items.append(data)
        return ListEnvSectionSchemaResult(items=items, total=page.total)
