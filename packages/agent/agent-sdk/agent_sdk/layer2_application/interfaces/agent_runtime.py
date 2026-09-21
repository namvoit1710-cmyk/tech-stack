from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class AgentRuntimeInterrupt:
    payload: Any


@dataclass
class AgentRuntimeResult:
    output: Any
    interrupted: bool = False
    interrupt_payload: AgentRuntimeInterrupt | None = None


@runtime_checkable
class IAgentRuntime(Protocol):
    async def invoke(
        self, state: dict, config: dict | None = None
    ) -> AgentRuntimeResult: ...

    async def resume(
        self,
        resume_value: Any,
        config: dict | None = None,
        interrupt_id: str | None = None,
    ) -> AgentRuntimeResult: ...

    async def stream(self, state: dict, config: dict | None = None): ...
