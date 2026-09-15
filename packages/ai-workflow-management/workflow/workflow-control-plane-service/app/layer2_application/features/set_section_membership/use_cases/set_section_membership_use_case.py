"""Replace a section's environment membership (SA-2605) — the Section↔Environment
many-to-many.

Replace-set: the Sections UI sends the full set of ticked environment ids. Each id
must resolve to an active environment of this tenant (an environment may belong to
several sections). Empty set is allowed (a section can be emptied). Section↔env
type-conflict validation (a key with two different types across an env's sections)
is a separate concern — SA-2613.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.section_membership import SectionMembership
from app.layer1_domain.exceptions import ValidationError
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)
from app.layer2_application.repositories.section_membership_repo_interface import (
    CASMismatch,
    ISectionMembershipRepo,
)


@dataclass
class SetSectionMembershipCommand:
    tenant_id: str = "system"
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    updated_by: str = ""


@dataclass
class SetSectionMembershipResult:
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> SetSectionMembershipResult:
    return SetSectionMembershipResult(
        error=message,
        error_code=ErrorCode.WORKFLOW_ENVIRONMENT_INVALID.code,
        error_details=details or {},
    )


def _key_type_conflict(message: str, **details: Any) -> SetSectionMembershipResult:
    return SetSectionMembershipResult(
        error=message,
        error_code=ErrorCode.KEY_TYPE_CONFLICT.code,
        error_details=details or {},
    )


class SetSectionMembershipUseCase:
    def __init__(
        self,
        logger: ILogger,
        section_membership_repo: ISectionMembershipRepo,
        environment_repo: IEnvironmentRepo,
        env_section_schema_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = section_membership_repo
        self.environment_repo = environment_repo
        self.section_schema_repo = env_section_schema_repo

    def _check_type_conflicts(
        self, *, tenant_id: str, section: str, env_ids: list[str]
    ) -> SetSectionMembershipResult | None:
        """SA-2613: for each env joining ``section``, a key in this section's schema
        must not have a different type in another section the env belongs to."""
        if self.section_schema_repo is None:
            return None
        try:
            target = self.section_schema_repo.get_by_natural_key(
                tenant_id=tenant_id, section=section
            )
        except Exception:
            target = None
        if target is None or not target.keys:
            return None
        target_types = {k.key: k.type for k in target.keys}
        schema_cache: dict[str, Any] = {}
        for env_id in env_ids:
            try:
                others = self.repo.list_sections_for_environment(
                    tenant_id=tenant_id, environment_id=env_id
                )
            except Exception:
                others = []
            for other in others:
                if other == section:
                    continue
                if other not in schema_cache:
                    try:
                        schema_cache[other] = self.section_schema_repo.get_by_natural_key(
                            tenant_id=tenant_id, section=other
                        )
                    except Exception:
                        schema_cache[other] = None
                osch = schema_cache[other]
                if osch is None:
                    continue
                for k in osch.keys:
                    if k.key in target_types and target_types[k.key] != k.type:
                        ta = getattr(target_types[k.key], "value", str(target_types[k.key]))
                        tb = getattr(k.type, "value", str(k.type))
                        return _key_type_conflict(
                            f"key '{k.key}' is '{ta}' in section '{section}' but "
                            f"'{tb}' in section '{other}'",
                            key=k.key, section_a=section, type_a=ta,
                            section_b=other, type_b=tb, environment_id=env_id,
                        )
        return None

    async def execute(self, command: SetSectionMembershipCommand) -> SetSectionMembershipResult:
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")
        section = (command.section or "").strip()
        if not section:
            return _invalid("a section is required")

        env_ids = [e for e in (command.environment_ids or []) if (e or "").strip()]

        # Every id must resolve to an active env of THIS tenant.
        for env_id in env_ids:
            env = self.environment_repo.get_by_id(env_id)
            if env is None or not env.is_active or env.tenant_id != command.tenant_id:
                return _invalid(
                    f"environment '{env_id}' does not exist for this tenant",
                    environment_id=env_id,
                )

        # SA-2613: same key must keep the same type across an env's sections.
        conflict = self._check_type_conflicts(
            tenant_id=command.tenant_id, section=section, env_ids=env_ids
        )
        if conflict is not None:
            return conflict

        existing = self.repo.get_by_section(tenant_id=command.tenant_id, section=section)
        try:
            if existing is not None:
                stored_version = existing.version
                existing.set_environments(
                    environment_ids=env_ids, updated_by=command.updated_by
                )
                self.repo.save(existing, expected_version=stored_version)
                entity = existing
            else:
                entity = SectionMembership.create(
                    tenant_id=command.tenant_id,
                    section=section,
                    environment_ids=env_ids,
                    created_by=command.updated_by,
                )
                self.repo.save(entity)
        except ValidationError as exc:
            return _invalid(str(exc))
        except CASMismatch as exc:
            return _invalid(str(exc))

        self.logger.info(
            "Section membership saved",
            section=entity.section, environment_ids=entity.environment_ids,
            tenant_id=entity.tenant_id,
        )
        return SetSectionMembershipResult(
            section=entity.section,
            environment_ids=list(entity.environment_ids),
            version=entity.version,
        )
