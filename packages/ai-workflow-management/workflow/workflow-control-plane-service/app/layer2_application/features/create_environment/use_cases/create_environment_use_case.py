"""Create an Environment (config variables). Orchestration only — rules live in
the domain (Environment.create / EnvVariable validation)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    CASMismatch,
    IEnvironmentRepo,
)
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    IEnvSectionSchemaRepo,
)
from app.layer2_application.services.env_section.schema_guard import (
    apply_section_schema,
)


@dataclass
class CreateEnvironmentCommand:
    tenant_id: str = "system"
    section: str = ""
    name: str = ""
    variables: list[dict] = field(default_factory=list)
    created_by: str = ""


@dataclass
class CreateEnvironmentResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> CreateEnvironmentResult:
    return CreateEnvironmentResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class CreateEnvironmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        environment_repo: IEnvironmentRepo,
        env_section_schema_repo: IEnvSectionSchemaRepo | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = environment_repo
        self.schema_repo = env_section_schema_repo

    async def execute(self, command: CreateEnvironmentCommand) -> CreateEnvironmentResult:
        if not command.created_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="created_by")
        try:
            variables = EnvVariable.list_from_dicts(command.variables)
            env = Environment.create(
                tenant_id=command.tenant_id,
                section=command.section,
                name=command.name,
                created_by=command.created_by,
                variables=variables,
            )
        except ValidationError as exc:
            return _invalid(str(exc))

        # SA-2471: if a section schema governs this section, the env keys must be
        # a subset of it; missing schema keys are inherited as empty; types come
        # from the schema. No schema -> legacy passthrough.
        guard = apply_section_schema(
            tenant_id=env.tenant_id, section=env.section,
            variables=env.variables, repo=self.schema_repo,
        )
        if not guard.ok:
            return CreateEnvironmentResult(
                error=ErrorCode.ENVIRONMENT_VARIABLES_INVALID.default_message,
                error_code=ErrorCode.ENVIRONMENT_VARIABLES_INVALID.code,
                error_details={"unknown_keys": guard.unknown_keys},
            )
        env.variables = guard.variables

        # Pre-check the natural key for a friendly 409 (the repo also enforces it).
        existing = self.repo.get_by_natural_key(
            tenant_id=env.tenant_id, section=env.section, name=env.name
        )
        if existing is not None and existing.is_active:
            return CreateEnvironmentResult(
                error=ErrorCode.ENVIRONMENT_ALREADY_EXISTS.default_message,
                error_code=ErrorCode.ENVIRONMENT_ALREADY_EXISTS.code,
                error_details={"section": env.section, "name": env.name},
            )
        try:
            self.repo.save(env)
        except CASMismatch as exc:
            return CreateEnvironmentResult(
                error=ErrorCode.ENVIRONMENT_ALREADY_EXISTS.default_message,
                error_code=ErrorCode.ENVIRONMENT_ALREADY_EXISTS.code,
                error_details={"detail": str(exc)},
            )
        self.logger.info(
            "Environment created", id=env.id, tenant_id=env.tenant_id,
            section=env.section, name=env.name, created_by=env.created_by,
        )
        return CreateEnvironmentResult(id=env.id, version=env.version)
