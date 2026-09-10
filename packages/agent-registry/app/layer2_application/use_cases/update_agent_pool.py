"""UC: update an agent pool's name/description (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import InvalidDataException, NotFoundException
from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class UpdateAgentPoolUseCase:
    """Rename/edit a pool. Guarded by `pool.update`."""

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, pool_id: str, name: str, description: str | None) -> AgentPoolDetail:
        name = (name or "").strip()
        if not name:
            raise InvalidDataException("Pool name is required", field="name")
        updated = self.repository.update_pool(pool_id, name, description)
        if updated is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)
        return updated
