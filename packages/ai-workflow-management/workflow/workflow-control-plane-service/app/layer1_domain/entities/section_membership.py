"""SectionMembership — the MANY-TO-MANY between a section and its environments
(SA-2605).

One aggregate per (tenant_id, section) holding the environment ids that belong to
the section. An environment id may appear under several sections, and a section may
hold many environments. This replaces the single ``AIW_ENVIRONMENT.SECTION`` pointer
(SA-2365/2471) as the source of truth for "which environments are in this section",
so a workflow bound to the section (see ``WorkflowSection``) may use any of them.

Invariants (single-aggregate): a section is required; environment ids are unique.
Zero environments is allowed (a section may be emptied via the membership editor);
the run guard is what rejects a run whose environment is not a current member.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe_keep_order(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        cleaned = (i or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


@dataclass(kw_only=True)
class SectionMembership(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    section: str = ""
    environment_ids: list[str] = field(default_factory=list)
    is_active: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        self.environment_ids = _dedupe_keep_order(list(self.environment_ids or []))
        if not self.created_at:
            self.created_at = _utc_now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ── Factory ──────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        section: str,
        environment_ids: list[str],
        created_by: str,
    ) -> "SectionMembership":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        entity = cls(
            tenant_id=(tenant_id or "").strip() or "system",
            section=(section or "").strip(),
            environment_ids=list(environment_ids or []),
            created_by=created_by,
            updated_by=created_by,
        )
        entity._ensure_valid()
        return entity

    # ── Behaviour ────────────────────────────────────────────────────
    def set_environments(self, *, environment_ids: list[str], updated_by: str) -> None:
        """Replace the membership set in one shot (the Sections UI always sends the
        full set of ticked environments)."""
        self.environment_ids = _dedupe_keep_order(list(environment_ids or []))
        self.touch(updated_by)

    def add_environment(self, *, environment_id: str, updated_by: str) -> None:
        cleaned = (environment_id or "").strip()
        if cleaned and cleaned not in self.environment_ids:
            self.environment_ids.append(cleaned)
            self.touch(updated_by)

    def remove_environment(self, *, environment_id: str, updated_by: str) -> None:
        cleaned = (environment_id or "").strip()
        if cleaned in self.environment_ids:
            self.environment_ids = [e for e in self.environment_ids if e != cleaned]
            self.touch(updated_by)

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    def has_environment(self, environment_id: str) -> bool:
        return (environment_id or "").strip() in self.environment_ids

    # ── Projections ──────────────────────────────────────────────────
    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "section": self.section,
            "environment_ids": list(self.environment_ids),
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Invariants ───────────────────────────────────────────────────
    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.tenant_id or not self.tenant_id.strip():
            errors.append("tenant_id is required")
        if not self.section or not self.section.strip():
            errors.append("a section is required")
        return errors

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))

    def touch(self, updated_by: str) -> None:
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by
