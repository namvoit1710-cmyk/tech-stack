"""Delete (soft) a section schema by id, cascading to its environments (SA-2471).

Soft-deleting a section schema also soft-deletes every ACTIVE environment in the
same (tenant, section) — an environment without a governing schema would be
orphaned. Returns how many environments were removed. Idempotent: a missing or
already-inactive schema reports deleted with a 0 cascade."""
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
class DeleteEnvSectionSchemaCommand:
    tenant_id: str = "system"
    id: str = ""
    deleted_by: str = ""


@dataclass
class DeleteEnvSectionSchemaResult:
    deleted: bool = False
    environments_deleted: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteEnvSectionSchemaUseCase:
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
        self, command: DeleteEnvSectionSchemaCommand
    ) -> DeleteEnvSectionSchemaResult:
        schema = self.repo.get_by_id(command.id)
        # Tenant isolation: a row owned by another tenant is "not found" here.
        if schema is not None and schema.tenant_id != command.tenant_id:
            return DeleteEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_NOT_FOUND.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_NOT_FOUND.code,
                error_details={"id": command.id},
            )

        cascaded = 0
        if schema is not None:
            page = self.env_repo.list_paged(
                ListEnvironmentsFilter(
                    tenant_id=schema.tenant_id,
                    section=schema.section,
                    include_inactive=False,
                    skip=0,
                    top=100000,
                )
            )
            for env in page.items:
                self.env_repo.soft_delete(env.id)
                cascaded += 1

        self.repo.soft_delete(command.id)
        self.logger.info(
            "Section schema deleted", id=command.id,
            environments_deleted=cascaded, deleted_by=command.deleted_by,
        )
        return DeleteEnvSectionSchemaResult(deleted=True, environments_deleted=cascaded)
