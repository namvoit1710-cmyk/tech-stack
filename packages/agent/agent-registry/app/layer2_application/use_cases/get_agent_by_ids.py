"""UC9: Get Agent by ID use case."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.agent_access_filter import filter_agents_by_visibility
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class GetAgentsByIdsUseCase:
    """Use case for retrieving agents by their IDs.
    
    Returns detailed agent information including configuration and metadata.
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository
    
    def execute(
        self, agent_ids: list[str], visible_ids: set[str] | None = None
    ) -> list[AgentResponseDTO]:
        """Get agents by their IDs, scoped by pool visibility.

        Args:
            agent_ids: List of unique identifiers of the agents
            visible_ids: Pool-visible agent ids (None = bypass → any).

        Returns:
            List of agent details as DTO (restricted to the visible ones)

        Raises:
            NotFoundException: If none of the requested agents exist / are visible.
        """
        agents = self.repository.find_by_ids(agent_ids)
        agents = filter_agents_by_visibility(agents, visible_ids)

        if not agents:
            raise NotFoundException(f"Agents with IDs '{agent_ids}' not found")

        return [AgentResponseDTO.from_entity(agent) for agent in agents]
