from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.base_entity import BaseEntity


@dataclass
class ExecutionPlan(BaseEntity):
    workflow_id: str = ""
    workflow_info: dict[str, Any] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)