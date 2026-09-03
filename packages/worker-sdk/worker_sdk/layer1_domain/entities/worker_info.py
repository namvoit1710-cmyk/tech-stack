from dataclasses import dataclass, field
from typing import Any

from worker_sdk.layer1_domain.entities.base_entity import BaseDomainEntity
from worker_sdk.layer1_domain.value_objects.worker_capability import WorkerCapability
from worker_sdk.layer1_domain.value_objects.port import default_task_ports


@dataclass(kw_only=True)
class WorkerInfo(BaseDomainEntity):
    worker_type: str
    version: str
    sdk_version: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    capabilities: list[WorkerCapability] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=default_task_ports)
