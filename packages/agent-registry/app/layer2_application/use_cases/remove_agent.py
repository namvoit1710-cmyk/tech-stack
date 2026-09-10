"""UC3: Remove Agent use case."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class RemoveAgentUseCase:
    """Use case for removing an agent from the registry.
    
    Business rule:
    - Agent must exist before deletion
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(self, agent_id: str) -> None:
        """Remove an agent from the registry (SOFT delete — status set to DELETED).

        The repository's `soft_delete` also clears the agent's pool links
        (`agent_pool_agents`) so no pool keeps a dangling reference (T5).

        Args:
            agent_id: ID of agent to remove

        Raises:
            NotFoundException: If agent doesn't exist
        """
        # Check if agent exists
        agent = self.repository.find_by_id(agent_id)
        if not agent:
            raise NotFoundException("Agent", entity_id=agent_id)

        # Soft-delete the agent (+ clear its pool links)
        self.repository.soft_delete(agent_id)
