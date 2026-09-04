from dataclasses import dataclass

from smart_service_sdk.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class PredictiveRequest(BaseAuditDomainEntity):
    subject: str
    description: str
    status: str = "planned"
