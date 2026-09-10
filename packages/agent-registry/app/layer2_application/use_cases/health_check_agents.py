"""Health check agents use case - periodically checks health of all agents."""

from datetime import datetime, timezone

from app.layer2_application.interfaces.health_check_client_port import IHealthCheckClient
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository
from app.layer2_application.interfaces.async_executor_port import AsyncExecutorInterface
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger


class HealthCheckAgentsUseCase:
    """Use case for performing health checks on all agents.
    
    This use case is intended to be run periodically (e.g., every minute) to update
    the health status of all agents in the registry.
    """

    def __init__(
        self,
        agent_repository: IAgentRepository,
        health_check_client: IHealthCheckClient,
        async_executor: AsyncExecutorInterface,
        logger: ILogger | None = None,
    ):
        """Initialize use case with dependencies.

        Args:
            agent_repository: Agent repository port implementation
            health_check_client: Health check client port implementation
            logger: Structured logger port (defaults to a no-op NullLogger)
        """
        self.agent_repository = agent_repository
        self.health_check_client = health_check_client
        self.async_executor = async_executor
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(self) -> None:
        """Perform health checks on all agents and update their status."""
        self._logger.info("health_check_started", message="Starting health check for all agents.")
        
        # Fetch all agents
        agents = await self.async_executor.run_sync(self.agent_repository.find_all)
        
        # Create a mapping of health check endpoints to agents for efficient lookup
        agents_endpoint_map = {
            agent.healthcheck_endpoint: agent for agent in agents if agent.can_be_health_checked() # Only Technical agents with health check endpoints should be checked
        }
        
        # Perform batch health check
        if not agents_endpoint_map:
            self._logger.info("health_check_no_agents", message="HealthCheck: No agent's endpoints to health check")
            return
        
        health_check_results = await self.health_check_client.batch_check_health(list(agents_endpoint_map.keys()))
        
        if health_check_results:
            for endpoint, is_alive in health_check_results.items():
                agent = agents_endpoint_map.get(endpoint)
                if agent:
                    if is_alive:
                        agent.mark_alive()
                        self._logger.info("health_check_alive", message=f"Agent {agent.name} is alive.")
                    else:
                        agent.mark_dead()
                        self._logger.info("health_check_dead", message=f"Agent {agent.name} is dead.")
                    await self.async_executor.run_sync(self.agent_repository.update_health_status, agent.id, agent.is_alive, agent.last_health_check_at)
        return