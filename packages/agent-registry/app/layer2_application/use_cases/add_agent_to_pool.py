"""UC: attach an agent to a pool (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository


class AddAgentToPoolUseCase:
    """Attach an agent to a pool (idempotent). Guarded by `pool.agent.add`.

    404 if the pool or the agent does not exist.
    """

    def __init__(self, pool_repository: IAgentPoolRepository, agent_repository: IAgentRepository):
        self.pool_repository = pool_repository
        self.agent_repository = agent_repository

    def execute(self, pool_id: str, agent_id: str) -> None:
        if self.pool_repository.get_pool(pool_id) is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        if self.agent_repository.find_by_id(agent_id) is None:
            raise NotFoundException("Agent", entity_id=agent_id)
        self.pool_repository.add_agent(pool_id, agent_id)
