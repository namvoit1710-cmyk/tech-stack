"""UC7: Get Available Agents use case."""

from app.layer2_application.agent_access_filter import filter_agents_by_visibility
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class GetAvailableAgentsUseCase:
    """Use case for determining available agents.
    
    Business rules:
    - Technical agents: available if they respond to health check
    - Business agents: available if status is 'active'
    - Inactive agents are not available
    - Unpublished agents are not available
    """

    def __init__(
        self, repository: IAgentRepository
    ):
        """Initialize use case with dependencies.
        
        Args:
            repository: Agent repository port implementation
            health_check_service: Health check service port implementation
        """
        self.repository = repository

    def execute(
        self,
        visible_ids: set[str] | None = None,
    ) -> list[AgentResponseDTO]:
        """Get available agents, scoped by RBAC pool visibility.

        Availability logic (published + active + alive) is preserved and applied AFTER the
        pool-visibility restriction (visible_ids None = bypass → all agents).
        """
        all_agents = self.repository.find_all()
        all_agents = filter_agents_by_visibility(all_agents, visible_ids)

        available_agents = [a for a in all_agents if a.is_available()]
        return AgentResponseDTO.from_entities(available_agents)
