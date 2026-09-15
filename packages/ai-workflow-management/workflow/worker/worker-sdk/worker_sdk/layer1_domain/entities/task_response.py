from dataclasses import dataclass, field
from typing import Any, Optional

from worker_sdk.layer1_domain.entities.base_entity import BaseDomainEntity
from worker_sdk.layer1_domain.value_objects.task_metrics import TaskMetrics
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


@dataclass(kw_only=True)
class TaskResponse(BaseDomainEntity):
    task_id: str
    status: TaskStatus
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metrics: Optional[TaskMetrics] = None
