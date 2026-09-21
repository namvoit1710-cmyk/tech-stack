from dataclasses import dataclass, field

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from app.layer2_application.interfaces.agent_registry_client import IAgentRegistryClient


@dataclass
class GetAvailableAgentsOutput:
    agents: list[dict] = field(default_factory=list)


class GetAvailableAgentsUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_registry_client: IAgentRegistryClient,
    ):
        self.logger = logger
        self.monitor = monitor
        self.agent_registry_client = agent_registry_client

    async def execute(self) -> GetAvailableAgentsOutput:
        agents = await self.agent_registry_client.filter_agents(is_published=True)
        payload = [
            {
                "id": agent.id,
                "name": agent.name,
                "kind": agent.kind,
                "description": agent.description,
                "status": agent.status,
                "is_published": agent.is_published,
            }
            for agent in agents
        ]
        self.logger.info("Loaded published agents", published_agent_count=len(payload))
        return GetAvailableAgentsOutput(agents=payload)