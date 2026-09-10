"""UC-PublishAgent: Publish an agent use case."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class PublishAgentUseCase:
    """Use case for publishing an agent.
    
    Business rules:
    - Agent must exist
    - Sets is_published to true
    - Status remains unchanged
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(self, agent_id: str) -> AgentResponseDTO:
        """Publish an agent.
        
        Args:
            agent_id: ID of agent to publish
            
        Returns:
            AgentResponseDTO: Updated agent details
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        # Retrieve existing agent
        agent = self.repository.find_by_id(agent_id)
        if not agent:
            raise NotFoundException(f"Agent with ID '{agent_id}' not found")

        # Update is_published to true
        updated = agent.update(is_published=True)

        # If no fields were updated, skip persistence and relationship updates
        if not updated:
            return AgentResponseDTO.from_entity(agent)

        # Persist changes
        self.repository.update(agent)
        
        # Return DTO
        return AgentResponseDTO.from_entity(agent)
