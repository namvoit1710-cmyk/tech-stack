from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from app.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class StageRecord(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    node_id: str | None = None
    task_id: str | None = None
    from_status: str = ""
    to_status: str = ""
    event_type: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
