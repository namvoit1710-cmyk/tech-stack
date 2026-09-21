from dataclasses import dataclass, field
from typing import Any
from app.layer1_domain.entities.base_entity import BaseEntity

from app.layer1_domain.constants.default_constant import DEFAULT_WORKFLOW_ID


@dataclass
class WorkflowDefinition(BaseEntity):
    workflow_id: str = DEFAULT_WORKFLOW_ID
    payload: dict[str, Any] = field(default_factory=dict)
