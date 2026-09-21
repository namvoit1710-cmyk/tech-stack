"""UC: create an agent pool (SA RBAC v1, Task 4)."""

from app.layer1_domain.exceptions import InvalidDataException
from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository


class CreateAgentPoolUseCase:
    """Create a pool. Guarded by `pool.create`. `created_by` = the caller's user id."""

    def __init__(self, repository: IAgentPoolRepository):
        self.repository = repository

    def execute(self, name: str, description: str | None, created_by: str | None) -> AgentPoolDetail:
        name = (name or "").strip()
        if not name:
            raise InvalidDataException("Pool name is required", field="name")
        return self.repository.create_pool(name, description, created_by)
