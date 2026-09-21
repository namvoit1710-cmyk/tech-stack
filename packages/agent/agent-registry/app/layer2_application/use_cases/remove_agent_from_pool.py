"""UC: detach an agent from a pool (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class RemoveAgentFromPoolUseCase:
    """Detach an agent from a pool (idempotent). Guarded by `pool.agent.remove`."""

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, pool_id: str, agent_id: str) -> None:
        if self.repository.get_pool(pool_id) is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        self.repository.remove_agent(pool_id, agent_id)
