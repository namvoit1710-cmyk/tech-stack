"""UC-DeactivateAgent: Deactivate an agent use case."""

from app.layer1_domain.entities.agent import AgentStatus
from app.layer1_domain.exceptions import (
    NotFoundException,
    InvalidOperationException,
)
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class DeactivateAgentUseCase:
    """Use case for deactivating an agent.
    
    Business rules:
    - Agent must exist
    - Agent must be published (is_published=true) to be deactivated
    - Sets status to INACTIVE
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(self, agent_id: str) -> AgentResponseDTO:
        """Deactivate an agent.
        
        Args:
            agent_id: ID of agent to deactivate
            
        Returns:
            AgentResponseDTO: Updated agent details
            
        Raises:
            NotFoundException: If agent doesn't exist
            InvalidOperationException: If agent is not published
        """
        # Retrieve existing agent
        agent = self.repository.find_by_id(agent_id)
        if not agent:
            raise NotFoundException("Agent", entity_id=agent_id)

        # Business rule: Only published agents can be deactivated
        if not agent.is_published:
            raise InvalidOperationException(
                f"Cannot deactivate agent '{agent.name}': agent must be published"
            )

        # Update status to INACTIVE
        updated = agent.update(status=AgentStatus.INACTIVE)

        # If no fields were updated, skip persistence and relationship updates
        if not updated:
            return AgentResponseDTO.from_entity(agent)

        # Persist changes
        self.repository.update(agent)
        
        # Return DTO
        return AgentResponseDTO.from_entity(agent)
