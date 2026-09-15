"""Delete (soft) an environment by id. Idempotent: an already-inactive or
missing row reports deleted rather than erroring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)


@dataclass
class DeleteEnvironmentCommand:
    tenant_id: str = "system"
    id: str = ""
    deleted_by: str = ""


@dataclass
class DeleteEnvironmentResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteEnvironmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        environment_repo: IEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = environment_repo

    async def execute(self, command: DeleteEnvironmentCommand) -> DeleteEnvironmentResult:
        env = self.repo.get_by_id(command.id)
        # Tenant isolation: a row owned by another tenant is "not found" here.
        if env is not None and env.tenant_id != command.tenant_id:
            return DeleteEnvironmentResult(
                error=ErrorCode.ENVIRONMENT_NOT_FOUND.default_message,
                error_code=ErrorCode.ENVIRONMENT_NOT_FOUND.code,
                error_details={"id": command.id},
            )
        self.repo.soft_delete(command.id)
        self.logger.info(
            "Environment deleted", id=command.id, deleted_by=command.deleted_by,
        )
        return DeleteEnvironmentResult(deleted=True)
