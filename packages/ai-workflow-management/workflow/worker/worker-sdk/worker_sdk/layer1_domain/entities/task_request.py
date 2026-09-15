from dataclasses import dataclass, field
from typing import Any, Optional

from worker_sdk.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class TaskRequest(BaseDomainEntity):
    task_id: str
    action: str
    inputs: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None
