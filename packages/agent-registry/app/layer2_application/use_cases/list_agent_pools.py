"""UC: list agent pools (visibility-aware) (SA RBAC v1, Task 4)."""

from app.layer1_domain.entities.principal import Principal
from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class ListAgentPoolsUseCase:
    """List active pools. Guarded by `pool.read`.

    `bypass_pool_visibility` → all pools; otherwise only the pools the caller is a member of.
    """

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, principal: Principal) -> list[AgentPoolDetail]:
        if principal.bypass_pool_visibility:
            return self.repository.list_pools()
        return self.repository.list_pools_for_user(principal.user_id)
