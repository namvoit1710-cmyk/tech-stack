"""UC-ActivateAgent: Activate an agent use case."""

from app.layer1_domain.entities.agent import AgentStatus
from app.layer1_domain.exceptions import (
    NotFoundException,
    InvalidOperationException,
)
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class ActivateAgentUseCase:
    """Use case for activating an agent.
    
    Business rules:
    - Agent must exist
    - Agent must be published (is_published=true) to be activated
    - Sets status to ACTIVE
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(self, agent_id: str) -> AgentResponseDTO:
        """Activate an agent.
        
        Args:
            agent_id: ID of agent to activate
            
        Returns:
            AgentResponseDTO: Updated agent details
            
        Raises:
            NotFoundException: If agent doesn't exist
            InvalidOperationException: If agent is not published
        """
        # Retrieve existing agent
        agent = self.repository.find_by_id(agent_id)
        if not agent:
            raise NotFoundException(f"Agent with ID '{agent_id}' not found")

        # Business rule: Only published agents can be activated
        if not agent.is_published:
            raise InvalidOperationException(
                f"Cannot activate agent '{agent.name}': agent must be published first"
            )

        # Update status to ACTIVE
        updated = agent.update(status=AgentStatus.ACTIVE)

        # If no fields were updated, skip persistence and relationship updates
        if not updated:
            return AgentResponseDTO.from_entity(agent)

        # Persist changes
        self.repository.update(agent)
        
        # Return DTO
        return AgentResponseDTO.from_entity(agent)
