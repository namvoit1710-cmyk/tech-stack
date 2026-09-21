from dataclasses import dataclass, field
from typing import Any

from agent_sdk.layer1_domain.entities.agent_runtime_config import AgentRuntimeConfig
from agent_sdk.layer1_domain.entities.execution_policy import ExecutionPolicy
from agent_sdk.layer1_domain.entities.queue_metadata import QueueMetadata


@dataclass
class AgentRegistration:
    agent_type: str
    version: str
    sdk_version: str
    domain: str
    endpoint_url: str
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    queue_name: str | None = None
    kind: str = "SERVICE"
    is_published: bool = True
    agent_runtime_config: AgentRuntimeConfig | None = None
    execution_policy: ExecutionPolicy | None = None
    attached_agent_ids: list[str] = field(default_factory=list)
    tool_ids: list[str] = field(default_factory=list)
    queue_metadata: QueueMetadata | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.queue_metadata is None and self.queue_name:
            self.queue_metadata = QueueMetadata(queue_name=self.queue_name)
        if self.queue_name is None and self.queue_metadata is not None:
            self.queue_name = self.queue_metadata.queue_name
