"""UC: soft-delete an agent pool (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class DeleteAgentPoolUseCase:
    """Soft-delete a pool (status='deleted') and clear its agent/member links.

    Guarded by `pool.delete`. Agents and users themselves are untouched.
    """

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, pool_id: str) -> None:
        if self.repository.get_pool(pool_id) is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        self.repository.soft_delete_pool(pool_id)
