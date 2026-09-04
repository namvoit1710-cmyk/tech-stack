from dataclasses import dataclass, field
from typing import Any

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class DuplicateResult(BaseAuditDomainEntity):
    record_id: str
    tenant_id: str = ""
    score: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    decision_trace: list[str] = field(default_factory=list)
