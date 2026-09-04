from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class BackgroundJobRecord(BaseAuditDomainEntity):
    tenant_id: str
    job_type: str
    status: str
    progress: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
