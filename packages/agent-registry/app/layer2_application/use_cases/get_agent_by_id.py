"""UC9: Get Agent by ID use case."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class GetAgentByIdUseCase:
    """Use case for retrieving a single agent by its ID.
    
    Returns detailed agent information including configuration and metadata.
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository
    
    def execute(self, agent_id: str, visible_ids: set[str] | None = None) -> AgentResponseDTO:
        """Get a specific agent by ID, scoped by pool visibility.

        Args:
            agent_id: Unique identifier of the agent
            visible_ids: Pool-visible agent ids (None = bypass → any agent).

        Returns:
            Agent details as DTO

        Raises:
            NotFoundException: If the agent doesn't exist OR is not visible to the caller
                (404 rather than 403 — do not leak the existence of a hidden agent).
        """
        agent = self.repository.find_by_id(agent_id)
        if not agent or (visible_ids is not None and agent.id not in visible_ids):
            raise NotFoundException(f"Agent with ID '{agent_id}' not found")
        return AgentResponseDTO.from_entity(agent)
