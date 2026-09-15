"""Get a single environment by id (tenant-scoped)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)


@dataclass
class GetEnvironmentCommand:
    tenant_id: str = "system"
    id: str = ""


@dataclass
class GetEnvironmentResult:
    environment: dict = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _not_found(env_id: str) -> GetEnvironmentResult:
    return GetEnvironmentResult(
        error=ErrorCode.ENVIRONMENT_NOT_FOUND.default_message,
        error_code=ErrorCode.ENVIRONMENT_NOT_FOUND.code,
        error_details={"id": env_id},
    )


class GetEnvironmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        environment_repo: IEnvironmentRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = environment_repo

    async def execute(self, command: GetEnvironmentCommand) -> GetEnvironmentResult:
        env = self.repo.get_by_id(command.id)
        if env is None or not env.is_active or env.tenant_id != command.tenant_id:
            return _not_found(command.id)
        return GetEnvironmentResult(environment=env.to_public_dict())
