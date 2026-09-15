"""Get a single section schema by id (tenant-scoped), with environment_count."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    IEnvSectionSchemaRepo,
)
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
    ListEnvironmentsFilter,
)


@dataclass
class GetEnvSectionSchemaCommand:
    tenant_id: str = "system"
    id: str = ""


@dataclass
class GetEnvSectionSchemaResult:
    section_schema: dict = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetEnvSectionSchemaUseCase:
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
        self, command: GetEnvSectionSchemaCommand
    ) -> GetEnvSectionSchemaResult:
        schema = self.repo.get_by_id(command.id)
        if schema is None or not schema.is_active or schema.tenant_id != command.tenant_id:
            return GetEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_NOT_FOUND.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_NOT_FOUND.code,
                error_details={"id": command.id},
            )
        count = self.env_repo.list_paged(
            ListEnvironmentsFilter(
                tenant_id=schema.tenant_id, section=schema.section,
                include_inactive=False, skip=0, top=1,
            )
        ).total
        data = schema.to_public_dict()
        data["environment_count"] = count
        return GetEnvSectionSchemaResult(section_schema=data)
