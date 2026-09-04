from dataclasses import dataclass, field
from typing import Any

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class CleansingResult(BaseAuditDomainEntity):
    record_id: str
    proposed_values: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
