"""UC: remove a member (user) from a pool (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class RemoveMemberFromPoolUseCase:
    """Remove a member from a pool (idempotent). Guarded by `pool.member.remove`.

    The user itself is untouched — only the pool membership is removed.
    """

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, pool_id: str, user_id: str) -> None:
        if self.repository.get_pool(pool_id) is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        self.repository.remove_member(pool_id, user_id)
