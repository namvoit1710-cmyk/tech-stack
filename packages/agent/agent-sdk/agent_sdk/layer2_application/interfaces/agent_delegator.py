from __future__ import annotations

from typing import Any, Protocol

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest


class IAgentDelegator(Protocol):
    async def delegate(self, request: AgentCallRequest) -> dict[str, Any]: ...
