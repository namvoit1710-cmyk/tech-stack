"""Update an environment (CAS via expected_version). Partial update: a None
field is left unchanged. Variable changes replace the whole list."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    apply_section_schemas,
)


@dataclass
class UpdateEnvironmentCommand:
    tenant_id: str = "system"
    id: str = ""
    expected_version: int = 0
    updated_by: str = ""
    new_section: str | None = None
    new_name: str | None = None
    new_variables: list[dict] | None = None


@dataclass
class UpdateEnvironmentResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> UpdateEnvironmentResult:
    return UpdateEnvironmentResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class UpdateEnvironmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        environment_repo: IEnvironmentRepo,
        env_section_schema_repo: IEnvSectionSchemaRepo | None = None,
        section_membership_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = environment_repo
        self.schema_repo = env_section_schema_repo
        self.section_membership_repo = section_membership_repo  # SA-2605 (M:N)

    def _env_sections(self, env: Any) -> list[str]:
        """Every section the env belongs to (SA-2605 M:N membership ∪ its own
        section pointer)."""
        sections: list[str] = []
        seen: set[str] = set()
        if self.section_membership_repo is not None:
            try:
                for s in self.section_membership_repo.list_sections_for_environment(
                    tenant_id=env.tenant_id, environment_id=env.id
                ):
                    if s not in seen:
                        seen.add(s)
                        sections.append(s)
            except Exception:
                pass
        if (env.section or "").strip() and env.section not in seen:
            sections.append(env.section)
        return sections

    async def execute(self, command: UpdateEnvironmentCommand) -> UpdateEnvironmentResult:
        if not command.expected_version:
            return UpdateEnvironmentResult(
                error=ErrorCode.PRECONDITION_REQUIRED.default_message,
                error_code=ErrorCode.PRECONDITION_REQUIRED.code,
            )
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")

        env = self.repo.get_by_id(command.id)
        if env is None or not env.is_active or env.tenant_id != command.tenant_id:
            return UpdateEnvironmentResult(
                error=ErrorCode.ENVIRONMENT_NOT_FOUND.default_message,
                error_code=ErrorCode.ENVIRONMENT_NOT_FOUND.code,
                error_details={"id": command.id},
            )

        by = command.updated_by
        try:
            if command.new_section is not None:
                env.set_section(command.new_section, updated_by=by)
            if command.new_name is not None:
                env.rename(command.new_name, updated_by=by)
            if command.new_variables is not None:
                env.set_variables(
                    EnvVariable.list_from_dicts(command.new_variables), updated_by=by
                )
        except ValidationError as exc:
            return _invalid(str(exc))

        # SA-2471/SA-2605: conform the (possibly changed) variables to the UNION of
        # the schemas of every section the env belongs to (many-to-many).
        if command.new_variables is not None:
            guard = apply_section_schemas(
                tenant_id=env.tenant_id, sections=self._env_sections(env),
                variables=env.variables, repo=self.schema_repo,
            )
            if not guard.ok:
                return UpdateEnvironmentResult(
                    error=ErrorCode.ENVIRONMENT_VARIABLES_INVALID.default_message,
                    error_code=ErrorCode.ENVIRONMENT_VARIABLES_INVALID.code,
                    error_details={"unknown_keys": guard.unknown_keys},
                )
            env.variables = guard.variables

        try:
            self.repo.save(env, expected_version=command.expected_version)
        except CASMismatch as exc:
            # Both a stale version and a natural-key collision surface here.
            code = (
                ErrorCode.ENVIRONMENT_ALREADY_EXISTS
                if "already exists" in str(exc)
                else ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT
            )
            return UpdateEnvironmentResult(
                error=code.default_message,
                error_code=code.code,
                error_details={"detail": str(exc)},
            )
        self.logger.info(
            "Environment updated", id=env.id, version=env.version, updated_by=env.updated_by,
        )
        return UpdateEnvironmentResult(id=env.id, version=env.version)
