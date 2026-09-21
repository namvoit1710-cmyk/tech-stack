from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.base_entity import BaseEntity


@dataclass
class WorkflowExecutionResult(BaseEntity):
    workflow_id: str = ""
    trigger_response: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)