from __future__ import annotations

from dataclasses import dataclass, field

from app.layer1_domain.entities.step import Step


@dataclass
class StepBatchRequest:
    message_id: str
    correlation_id: str
    conv_id: str
    batch_id: str
    from_agent: str
    to_agent: str
    steps: list[Step] = field(default_factory=list)
