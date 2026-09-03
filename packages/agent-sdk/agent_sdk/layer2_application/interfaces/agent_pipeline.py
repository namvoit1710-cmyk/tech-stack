from typing import Protocol

from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
from agent_sdk.layer1_domain.entities.agent_response import AgentResponse


class IAgentPipeline(Protocol):
    async def execute(self, request: AgentRequest) -> AgentResponse: ...
