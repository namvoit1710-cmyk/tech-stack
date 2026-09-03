from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LLMUsageRecord:
    event: str = "llm.usage"
    agent_type: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    thread_id: str = ""
    correlation_id: str = ""
    provider: str = ""
    model: str = ""
    operation: str = "chat"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_tokens: bool = False
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    currency: str = "USD"
    latency_ms: int = 0
    success: bool = True
    error_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
