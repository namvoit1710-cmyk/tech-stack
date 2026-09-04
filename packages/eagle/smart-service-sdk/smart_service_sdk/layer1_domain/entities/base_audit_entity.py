from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from smart_service_sdk.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class BaseAuditDomainEntity(BaseDomainEntity):
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
