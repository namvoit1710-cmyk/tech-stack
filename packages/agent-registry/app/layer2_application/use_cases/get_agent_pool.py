"""UC: get an agent pool with its agents + members (SA RBAC v1, Task 4)."""

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.rbac_read_models import AgentPoolWithLinks
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class GetAgentPoolUseCase:
    """Return a pool + its agent/member ids. Guarded by `pool.read`.

    Visibility: a caller with `bypass_pool_visibility` (is_super/system or `agent.view_all`)
    sees any pool; otherwise only pools they are a member of. A hidden pool returns 404
    (do not leak its existence).
    """

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, pool_id: str, principal: Principal) -> AgentPoolWithLinks:
        pool = self.repository.get_pool(pool_id)
        if pool is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        if not principal.bypass_pool_visibility and not self.repository.is_member(pool_id, principal.user_id):
            raise NotFoundException("AgentPool", entity_id=pool_id)
        return AgentPoolWithLinks(
            pool=pool,
            agent_ids=tuple(self.repository.list_agent_ids(pool_id)),
            member_ids=tuple(self.repository.list_member_ids(pool_id)),
        )
