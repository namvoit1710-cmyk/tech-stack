from agent_sdk.layer2_application.interfaces.agent_endpoint_resolver import (
    IAgentEndpointResolver,
)
from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry


class RegistryEndpointResolver(IAgentEndpointResolver):
    def __init__(self, registry: IAgentRegistry) -> None:
        self._registry = registry

    async def resolve_endpoint(self, agent_id: str) -> str:
        agents = await self._registry.list_active_agents()
        for agent in agents:
            current_id = agent.get("id") or agent.get("agent_id")
            if current_id != agent_id:
                continue

            endpoint = agent.get("invoke_endpoint")
            if endpoint:
                return endpoint

            metadata = agent.get("metadata") or {}
            if isinstance(metadata, dict):
                endpoint = metadata.get("endpoint_url")
                if endpoint:
                    return endpoint

            endpoint = agent.get("configuration", {}).get("endpoint_url")
            if endpoint:
                return endpoint

            endpoint = agent.get("endpoint_url")
            if endpoint:
                return endpoint

            health_endpoint = (
                agent.get("healthcheck_endpoint") or agent.get("health_endpoint") or ""
            )
            if health_endpoint.endswith("/health"):
                return health_endpoint.removesuffix("/health")

            raise ValueError(f"No endpoint URL found for active agent_id '{agent_id}'")

        raise ValueError(f"No active agent found with agent_id '{agent_id}'")
