from dataclasses import dataclass
from typing import Optional

from app.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class BaseAuditDomainEntity(BaseDomainEntity):
    created_at: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
