"""UC-UnpublishAgent: Unpublish an agent use case."""

from app.layer1_domain.entities.agent import AgentStatus
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class UnpublishAgentUseCase:
    """Use case for unpublishing an agent.
    
    Business rules:
    - Agent must exist
    - Sets is_published to false
    - Sets status to INACTIVE (unpublished agents cannot be active)
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(self, agent_id: str) -> AgentResponseDTO:
        """Unpublish an agent.
        
        Args:
            agent_id: ID of agent to unpublish
            
        Returns:
            AgentResponseDTO: Updated agent details
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        # Retrieve existing agent
        agent = self.repository.find_by_id(agent_id)
        if not agent:
            raise NotFoundException("Agent", entity_id=agent_id)

        # Update is_published to false and status to INACTIVE
        # Business rule: When published = false, status must be inactive
        updated = agent.update(
            is_published=False,
            status=AgentStatus.INACTIVE,
        )
        
        # If no fields were updated, skip persistence and relationship updates
        if not updated:
            return AgentResponseDTO.from_entity(agent)

        # Persist changes
        self.repository.update(agent)
        
        # Return DTO
        return AgentResponseDTO.from_entity(agent)
