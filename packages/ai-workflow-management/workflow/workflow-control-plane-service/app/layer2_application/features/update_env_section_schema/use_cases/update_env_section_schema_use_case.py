"""Update a section-level variable KEY SCHEMA (SA-2471, CAS via expected_version).

Replaces the whole key set. On success RECONCILES every active environment in
the section to the new key set (see services.env_section.reconcile)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.env_section_schema import SchemaKey
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    CASMismatch,
    IEnvSectionSchemaRepo,
)
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
    ListEnvironmentsFilter,
)
from app.layer2_application.services.env_section.reconcile import (
    reconcile_environment_variables,
    reconcile_section,
)


@dataclass
class UpdateEnvSectionSchemaCommand:
    tenant_id: str = "system"
    id: str = ""
    expected_version: int = 0
    updated_by: str = ""
    new_section: str | None = None
    new_keys: list[dict] | None = None


@dataclass
class UpdateEnvSectionSchemaResult:
    id: str = ""
    version: int = 0
    environments_reconciled: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class UpdateEnvSectionSchemaUseCase:
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
        self, command: UpdateEnvSectionSchemaCommand
    ) -> UpdateEnvSectionSchemaResult:
        if not command.expected_version:
            return UpdateEnvSectionSchemaResult(
                error=ErrorCode.PRECONDITION_REQUIRED.default_message,
                error_code=ErrorCode.PRECONDITION_REQUIRED.code,
            )
        if not command.updated_by.strip():
            return UpdateEnvSectionSchemaResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "updated_by"},
            )

        schema = self.repo.get_by_id(command.id)
        if schema is None or not schema.is_active or schema.tenant_id != command.tenant_id:
            return UpdateEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_NOT_FOUND.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_NOT_FOUND.code,
                error_details={"id": command.id},
            )

        # SA-2450 W18: rename the section. Capture the old name so we can re-tag
        # its environments once the schema save (which enforces (tenant, section)
        # uniqueness) succeeds.
        old_section = schema.section
        if command.new_section is not None:
            try:
                schema.rename(command.new_section, updated_by=command.updated_by)
            except ValidationError as exc:
                return UpdateEnvSectionSchemaResult(
                    error=str(exc),
                    error_code=ErrorCode.VALIDATION_ERROR.code,
                    error_details={"field": "section"},
                )

        if command.new_keys is not None:
            candidate = SchemaKey.list_from_dicts(command.new_keys)
            # Validate BEFORE mutating so we can return structured 422 details.
            key_errors = schema._validate_keys(candidate)
            if not key_errors.is_ok():
                return UpdateEnvSectionSchemaResult(
                    error=ErrorCode.SECTION_SCHEMA_INVALID_KEYS.default_message,
                    error_code=ErrorCode.SECTION_SCHEMA_INVALID_KEYS.code,
                    error_details=key_errors.to_dict(),
                )
            schema.set_keys(candidate, updated_by=command.updated_by)

        try:
            self.repo.save(schema, expected_version=command.expected_version)
        except CASMismatch as exc:
            code = (
                ErrorCode.SECTION_SCHEMA_ALREADY_EXISTS
                if "already exists" in str(exc)
                else ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT
            )
            return UpdateEnvSectionSchemaResult(
                error=code.default_message,
                error_code=code.code,
                error_details={"detail": str(exc)},
            )

        renamed = schema.section != old_section
        if renamed:
            # Move every env from the OLD section name to the new one, conforming
            # each to the (possibly also-updated) key schema in the same save.
            reconciled = self._retag_environments(old_section, schema)
        else:
            reconciled = reconcile_section(schema, self.env_repo)
        self.logger.info(
            "Section schema updated", id=schema.id, version=schema.version,
            environments_reconciled=reconciled, updated_by=schema.updated_by,
        )
        return UpdateEnvSectionSchemaResult(
            id=schema.id, version=schema.version, environments_reconciled=reconciled
        )

    def _retag_environments(self, old_section: str, schema: Any) -> int:
        """SA-2450 W18: move every active environment from ``old_section`` to the
        schema's (new) section name and conform it to the key schema, in one save
        per env. Workflow assignments follow automatically because the assignment
        read derives the section from these envs' current section (SA-2481)."""
        page = self.env_repo.list_paged(
            ListEnvironmentsFilter(
                tenant_id=schema.tenant_id,
                section=old_section,
                include_inactive=False,
                skip=0,
                top=100000,
            )
        )
        saved = 0
        for env in page.items:
            expected = env.version
            env.section = schema.section
            env.variables = reconcile_environment_variables(schema, env.variables)
            env.touch(updated_by=schema.updated_by or schema.created_by or "system")
            self.env_repo.save(env, expected_version=expected)
            saved += 1
        return saved
