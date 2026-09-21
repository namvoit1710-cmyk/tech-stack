"""UC8: Get All Agents use case."""

from app.layer2_application.agent_access_filter import filter_agents_by_visibility
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class GetAllAgentsUseCase:
    """Use case for retrieving all agents without health checks.
    
    This is a simple read operation that returns all agents
    without performing any health checks.
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository
    
    def execute(
        self,
        visible_ids: set[str] | None = None,
    ) -> list[AgentResponseDTO]:
        """Get all agents, scoped by RBAC pool visibility.

        Args:
            visible_ids: Pool-visible agent ids (None = bypass → all agents).

        Returns:
            List of agents as DTOs
        """
        agents = self.repository.find_all()
        agents = filter_agents_by_visibility(agents, visible_ids)
        return AgentResponseDTO.from_entities(agents)
