from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.step import (
    ResolvedCapability,
    StepResult,
    DispatchStepRequest,
)


class ITechnicalAgentStepExecutor(Protocol):
    async def execute(
        self,
        request: DispatchStepRequest,
        capability: ResolvedCapability,
    ) -> StepResult: ...
