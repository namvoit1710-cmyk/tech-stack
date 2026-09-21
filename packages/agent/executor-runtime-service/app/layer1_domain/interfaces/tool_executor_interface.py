from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.step import Step, StepResult


class IToolExecutor(Protocol):
    async def execute(self, tool_name: str, step: Step) -> StepResult: ...
