from dataclasses import dataclass, field
from typing import Any

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class BackgroundJobMetadata(BaseAuditDomainEntity):
    status: str = "submitted"
    job_type: str = "unknown"
    progress: int = 0
    details: dict[str, Any] = field(default_factory=dict)
