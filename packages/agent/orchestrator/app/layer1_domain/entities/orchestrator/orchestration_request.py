from dataclasses import dataclass, field


@dataclass
class OrchestrationRequest:
    message: str
    conversation_id: str = ""
    user_id: str = "anonymous"
    metadata: dict = field(default_factory=dict)
