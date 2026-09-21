from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass

from app.layer1_domain.entities.step import Step


@dataclass(frozen=True, slots=True)
class StepStatusPublisherInput:
    step: Step
    to_agent: str
    conv_id: str
    correlation_id: str

class IStepStatusPublisher(Protocol):
    async def publish_status(
        self,
        input: StepStatusPublisherInput,
    ) -> None: ...
