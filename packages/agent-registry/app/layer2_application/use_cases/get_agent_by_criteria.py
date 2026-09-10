"""UC4: Get Agent by Criteria use case."""

from app.layer1_domain.entities.agent import AgentKind, AgentStatus
from app.layer2_application.agent_access_filter import filter_agents_by_visibility
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class GetAgentByCriteriaUseCase:
    """Use case for querying agents by various criteria.
    
    Supports filtering by: ID, status, kind, is_alive, is_published.
    """

    def __init__(self, repository: IAgentRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Agent repository port implementation
        """
        self.repository = repository

    def execute(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        is_alive: bool | None = None,
        is_published: bool | None = None,
        visible_ids: set[str] | None = None,
    ) -> list[AgentResponseDTO]:
        """Query agents by criteria.
        
        Args:
            agent_id: Filter by agent ID
            status: Filter by status (active, inactive)
            kind: Filter by kind (business, technical)
            is_alive: Filter by liveness status
            is_published: Filter by published status
            
        Returns:
            List of agents matching criteria as DTOs
        """
        # Parse enum values if provided
        status_enum = AgentStatus(status) if status else None
        kind_enum = AgentKind(kind) if kind else None

        # Query repository
        agents = self.repository.find_by_criteria(
            agent_id=agent_id,
            status=status_enum,
            kind=kind_enum,
            is_alive=is_alive,
            is_published=is_published,
        )

        # Scope to pool-visible agents (None = bypass → all)
        agents = filter_agents_by_visibility(agents, visible_ids)

        return AgentResponseDTO.from_entities(agents)
