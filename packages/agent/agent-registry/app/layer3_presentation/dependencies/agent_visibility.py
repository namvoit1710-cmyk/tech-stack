"""Agent read guard + pool-visibility dependency (SA RBAC v1, Task 5).

`get_visible_agent_ids` gates an agent-read endpoint on `agent.read` AND resolves the set
of agent ids the caller may see: None when the caller bypasses pool visibility
(is_super / system / holds `agent.view_all`), otherwise the ids of agents in the caller's
pools (via `find_agent_ids_visible_to`). Endpoints pass the result to the read use cases.
"""

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from app.layer1_domain.entities.principal import Principal
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository
from app.layer3_presentation.dependencies.principal import require_permission
from container import Container


@inject
async def get_visible_agent_ids(
    principal: Principal = Depends(require_permission("agent.read")),
    pool_repository: IAgentPoolRepository = Depends(Provide[Container.agent_pool_repository]),
) -> set[str] | None:
    """Guard `agent.read`; return the visible agent-id set, or None to bypass the filter."""
    if principal.bypass_pool_visibility:
        return None
    return pool_repository.find_agent_ids_visible_to(principal.user_id)
