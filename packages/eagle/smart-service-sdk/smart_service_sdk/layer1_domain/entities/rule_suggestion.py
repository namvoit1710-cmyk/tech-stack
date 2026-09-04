from dataclasses import dataclass, field

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class RuleSuggestion(BaseAuditDomainEntity):
    request_id: str
    suggestions: list[str] = field(default_factory=list)
    rationale: str = ""
