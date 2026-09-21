from typing import Protocol


class IAgentEndpointResolver(Protocol):
    async def resolve_endpoint(self, agent_id: str) -> str: ...
