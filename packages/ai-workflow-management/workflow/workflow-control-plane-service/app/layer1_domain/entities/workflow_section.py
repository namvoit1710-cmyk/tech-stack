"""WorkflowSection — binds a workflow to EXACTLY ONE section (SA-2605).

Supersedes ``WorkflowEnvironmentAssignment`` (SA-2450): a workflow no longer picks
a subset of environments. It binds to a single ``section``; at run time it may use
ANY environment of that section (membership lives in ``SectionMembership``). This
removes the per-flow environment picking that made a parent/sub-flow env mismatch
possible — a parent and its sub-flow bound to the same section always share the
same environments.

Invariant (single-aggregate): a section is required. The run-time rule "the chosen
environment belongs to the bound section" is a cross-aggregate check that lives in
the run guard (needs the SectionMembership repo).

Rich domain entity following the WorkflowEnvironmentAssignment/Environment pattern:
every mutation goes through a behaviour method that ``touch``-es the CAS/audit
metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class WorkflowSection(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    workflow_id: str = ""
    section: str = ""                 # the ONE section this workflow binds to
    is_active: bool = True
    version: int = 1

    def __post_init__(self) -> None:
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
        workflow_id: str,
        section: str,
        created_by: str,
    ) -> "WorkflowSection":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        entity = cls(
            tenant_id=(tenant_id or "").strip() or "system",
            workflow_id=(workflow_id or "").strip(),
            section=(section or "").strip(),
            created_by=created_by,
            updated_by=created_by,
        )
        entity._ensure_valid()
        return entity

    # ── Behaviour ────────────────────────────────────────────────────
    def set_section(self, *, section: str, updated_by: str) -> None:
        """Rebind to a (possibly different) section. Validates the invariant."""
        new_section = (section or "").strip()
        self._validate(section=new_section, workflow_id=self.workflow_id)
        self.section = new_section
        self.touch(updated_by)

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    # ── Projections ──────────────────────────────────────────────────
    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "section": self.section,
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Invariants ───────────────────────────────────────────────────
    @staticmethod
    def _validate(*, section: str, workflow_id: str) -> None:
        errors: list[str] = []
        if not workflow_id or not workflow_id.strip():
            errors.append("workflow_id is required")
        if not section or not section.strip():
            errors.append("a section is required")
        if errors:
            raise ValidationError("; ".join(errors))

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.tenant_id or not self.tenant_id.strip():
            errors.append("tenant_id is required")
        if not self.workflow_id or not self.workflow_id.strip():
            errors.append("workflow_id is required")
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
