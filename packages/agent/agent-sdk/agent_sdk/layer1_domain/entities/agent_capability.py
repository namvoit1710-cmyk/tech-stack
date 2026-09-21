from dataclasses import dataclass, field

from agent_sdk.layer1_domain.entities.queue_metadata import QueueMetadata


@dataclass
class AgentCapability:
    agent_type: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    required_parameters: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    timeout_seconds: float = 300.0
    enabled: bool = True
    required_confirmation: bool = True
    semantic_intents: list[str] = field(default_factory=list)
    queue_metadata: QueueMetadata | None = None
