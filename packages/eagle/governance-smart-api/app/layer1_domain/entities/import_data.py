from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(frozen=True)
class GovernanceImportRequest:
    file_ids: list[str]
    tenant_id: str | None = None


@dataclass(frozen=True)
class GovernanceImportAcceptance:
    job_id: str
    tenant_id: str
    status: str
    file_ids: list[str]
    accepted_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str = ""
    file_results: list[dict[str, object]] = field(default_factory=list)


@dataclass(kw_only=True)
class GovernanceUploadResult(BaseAuditDomainEntity):
    file_name: str
    content_hash: str
    tenant_id: str | None = None
    row_count: int = 0
    raw_headers: list[str] = field(default_factory=list)
    parsed_rows: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class GovernanceUploadAcceptance:
    result_id: str
    file_name: str
    content_hash: str
    tenant_id: str | None = None
    row_count: int = 0
    raw_headers: list[str] = field(default_factory=list)
    parsed_rows: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime | None = None
