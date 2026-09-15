"""Create a section-level variable KEY SCHEMA (SA-2471).

Orchestration only — the key rules live in the domain (EnvSectionSchema.create /
structured validate). On success this RECONCILES every existing environment in
the section to the new key set (see services.env_section.reconcile)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.env_section_schema import EnvSectionSchema, SchemaKey
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    CASMismatch,
    IEnvSectionSchemaRepo,
)
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)
from app.layer2_application.services.env_section.reconcile import reconcile_section


@dataclass
class CreateEnvSectionSchemaCommand:
    tenant_id: str = "system"
    section: str = ""
    keys: list[dict] = field(default_factory=list)
    created_by: str = ""


@dataclass
class CreateEnvSectionSchemaResult:
    id: str = ""
    version: int = 0
    environments_reconciled: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class CreateEnvSectionSchemaUseCase:
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
        self, command: CreateEnvSectionSchemaCommand
    ) -> CreateEnvSectionSchemaResult:
        if not command.created_by.strip():
            return CreateEnvSectionSchemaResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "created_by"},
            )

        keys = SchemaKey.list_from_dicts(command.keys)
        # Build the entity without raising so we can return STRUCTURED key errors.
        schema = EnvSectionSchema(
            tenant_id=(command.tenant_id or "").strip() or "system",
            section=(command.section or "").strip(),
            keys=keys,
            created_by=command.created_by,
            updated_by=command.created_by,
        )
        key_errors = schema.validate()
        if not key_errors.is_ok():
            return CreateEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_INVALID_KEYS.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_INVALID_KEYS.code,
                error_details=key_errors.to_dict(),
            )

        # Pre-check the natural key for a friendly 409 (the repo also enforces it).
        existing = self.repo.get_by_natural_key(
            tenant_id=schema.tenant_id, section=schema.section
        )
        if existing is not None and existing.is_active:
            return CreateEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_ALREADY_EXISTS.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_ALREADY_EXISTS.code,
                error_details={"section": schema.section},
            )

        try:
            self.repo.save(schema)
        except CASMismatch as exc:
            return CreateEnvSectionSchemaResult(
                error=ErrorCode.SECTION_SCHEMA_ALREADY_EXISTS.default_message,
                error_code=ErrorCode.SECTION_SCHEMA_ALREADY_EXISTS.code,
                error_details={"detail": str(exc)},
            )

        reconciled = reconcile_section(schema, self.env_repo)
        self.logger.info(
            "Section schema created", id=schema.id, tenant_id=schema.tenant_id,
            section=schema.section, key_count=len(schema.keys),
            environments_reconciled=reconciled, created_by=schema.created_by,
        )
        return CreateEnvSectionSchemaResult(
            id=schema.id, version=schema.version, environments_reconciled=reconciled
        )
