from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional
from app.layer1_domain.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class BaseAuditDomainEntity(BaseDomainEntity):
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: Optional[datetime] = None
