"""WorkflowEnvironmentAssignment — binds a workflow to ONE section and a subset
(≥1) of that section's environments (SA-2450).

Sibling of the SA-2365 Environment aggregate. Where an Environment defines the
variables, this aggregate records *which* environments a given workflow may run
against: exactly one ``section`` and one-or-more ``environment_ids`` drawn from
that section. When the workflow runs, only these environments are offered, and
the run guard rejects any other ``environment_id`` (an unassigned workflow shows
an empty selector — see SA-2450 W6/W7/W8).

Invariants enforced here (single-aggregate): a section is required and at least
one environment id is required (never zero), and ids are unique. The cross-
aggregate rule "every id belongs to ``section``" needs the Environment repo and
therefore lives in ``SetWorkflowEnvironmentAssignmentUseCase``.

Rich domain entity following the Environment/GatewayApp pattern: every mutation
goes through a behaviour method that validates the invariant and ``touch``-es the
CAS/audit metadata.
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
class WorkflowEnvironmentAssignment(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    workflow_id: str = ""
    section: str = ""                              # the ONE section this workflow binds to
    environment_ids: list[str] = field(default_factory=list)  # subset of that section (≥1)
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
        workflow_id: str,
        section: str,
        environment_ids: list[str],
        created_by: str,
    ) -> "WorkflowEnvironmentAssignment":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        entity = cls(
            tenant_id=(tenant_id or "").strip() or "system",
            workflow_id=(workflow_id or "").strip(),
            section=(section or "").strip(),
            environment_ids=list(environment_ids or []),
            created_by=created_by,
            updated_by=created_by,
        )
        entity._ensure_valid()
        return entity

    # ── Behaviour ────────────────────────────────────────────────────
    def set_binding(
        self, *, section: str, environment_ids: list[str], updated_by: str
    ) -> None:
        """Replace the section + environment subset in one shot (the Assign UI
        always sends the full set). Validates the ≥1 invariant."""
        new_section = (section or "").strip()
        new_ids = _dedupe_keep_order(list(environment_ids or []))
        self._validate(section=new_section, workflow_id=self.workflow_id, environment_ids=new_ids)
        self.section = new_section
        self.environment_ids = new_ids
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
            "environment_ids": list(self.environment_ids),
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Invariants ───────────────────────────────────────────────────
    @staticmethod
    def _validate(*, section: str, workflow_id: str, environment_ids: list[str]) -> None:
        errors: list[str] = []
        if not workflow_id or not workflow_id.strip():
            errors.append("workflow_id is required")
        if not section or not section.strip():
            errors.append("a section is required")
        if not environment_ids:
            errors.append("at least one environment is required")
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
        if not self.environment_ids:
            errors.append("at least one environment is required")
        return errors

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))

    def touch(self, updated_by: str) -> None:
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by
