"""Agent pools router — CRUD, attach/detach agents, add/remove members (SA RBAC v1, Task 4)."""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.rbac import (
    POOL_AGENT_ADD,
    POOL_AGENT_REMOVE,
    POOL_CREATE,
    POOL_DELETE,
    POOL_MEMBER_ADD,
    POOL_MEMBER_REMOVE,
    POOL_READ,
    POOL_UPDATE,
)
from app.layer2_application.use_cases.add_agent_to_pool import AddAgentToPoolUseCase
from app.layer2_application.use_cases.add_member_to_pool import AddMemberToPoolUseCase
from app.layer2_application.use_cases.create_agent_pool import CreateAgentPoolUseCase
from app.layer2_application.use_cases.delete_agent_pool import DeleteAgentPoolUseCase
from app.layer2_application.use_cases.get_agent_pool import GetAgentPoolUseCase
from app.layer2_application.use_cases.list_agent_pools import ListAgentPoolsUseCase
from app.layer2_application.use_cases.remove_agent_from_pool import RemoveAgentFromPoolUseCase
from app.layer2_application.use_cases.remove_member_from_pool import RemoveMemberFromPoolUseCase
from app.layer2_application.use_cases.update_agent_pool import UpdateAgentPoolUseCase
from app.layer3_presentation.dependencies.principal import get_bearer_token, require_permission
from app.layer3_presentation.schemas.agent_pool_schema import (
    AddAgentRequest,
    AddMemberRequest,
    AgentPoolDetailResponse,
    AgentPoolListResponse,
    AgentPoolResponse,
    CreatePoolRequest,
    UpdatePoolRequest,
)
from app.layer3_presentation.schemas.rbac_user_schema import UserResponse
from container import Container

router = APIRouter(prefix="/api/v1/agents-registry", tags=["agent-pools"])


@router.post("/agent-pools", status_code=status.HTTP_201_CREATED, response_model=AgentPoolResponse)
@inject
async def create_pool(
    request: CreatePoolRequest,
    principal: Principal = Depends(require_permission(POOL_CREATE)),
    use_case: CreateAgentPoolUseCase = Depends(Provide[Container.create_agent_pool_use_case]),
) -> AgentPoolResponse:
    """Create a pool (created_by = the caller's user id)."""
    return AgentPoolResponse.from_detail(
        use_case.execute(name=request.name, description=request.description, created_by=principal.user_id)
    )


@router.get("/agent-pools", response_model=AgentPoolListResponse)
@inject
async def list_pools(
    principal: Principal = Depends(require_permission(POOL_READ)),
    use_case: ListAgentPoolsUseCase = Depends(Provide[Container.list_agent_pools_use_case]),
) -> AgentPoolListResponse:
    """List pools — all if the caller bypasses pool visibility, else only their own."""
    pools = use_case.execute(principal)
    return AgentPoolListResponse(pools=[AgentPoolResponse.from_detail(p) for p in pools], total=len(pools))


@router.get("/agent-pools/{pool_id}", response_model=AgentPoolDetailResponse)
@inject
async def get_pool(
    pool_id: str,
    principal: Principal = Depends(require_permission(POOL_READ)),
    use_case: GetAgentPoolUseCase = Depends(Provide[Container.get_agent_pool_use_case]),
) -> AgentPoolDetailResponse:
    """Get a pool + its agents/members (404 if not visible to the caller)."""
    return AgentPoolDetailResponse.from_view(use_case.execute(pool_id, principal))


@router.put("/agent-pools/{pool_id}", response_model=AgentPoolResponse)
@inject
async def update_pool(
    pool_id: str,
    request: UpdatePoolRequest,
    _: Principal = Depends(require_permission(POOL_UPDATE)),
    use_case: UpdateAgentPoolUseCase = Depends(Provide[Container.update_agent_pool_use_case]),
) -> AgentPoolResponse:
    return AgentPoolResponse.from_detail(
        use_case.execute(pool_id=pool_id, name=request.name, description=request.description)
    )


@router.delete("/agent-pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_pool(
    pool_id: str,
    _: Principal = Depends(require_permission(POOL_DELETE)),
    use_case: DeleteAgentPoolUseCase = Depends(Provide[Container.delete_agent_pool_use_case]),
) -> None:
    """Soft-delete a pool and clear its agent/member links."""
    use_case.execute(pool_id)


@router.post("/agent-pools/{pool_id}/agents", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def add_agent(
    pool_id: str,
    request: AddAgentRequest,
    _: Principal = Depends(require_permission(POOL_AGENT_ADD)),
    use_case: AddAgentToPoolUseCase = Depends(Provide[Container.add_agent_to_pool_use_case]),
) -> None:
    use_case.execute(pool_id, request.agent_id)


@router.delete("/agent-pools/{pool_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def remove_agent(
    pool_id: str,
    agent_id: str,
    _: Principal = Depends(require_permission(POOL_AGENT_REMOVE)),
    use_case: RemoveAgentFromPoolUseCase = Depends(Provide[Container.remove_agent_from_pool_use_case]),
) -> None:
    use_case.execute(pool_id, agent_id)


@router.post("/agent-pools/{pool_id}/members", response_model=UserResponse)
@inject
async def add_member(
    pool_id: str,
    request: AddMemberRequest,
    _: Principal = Depends(require_permission(POOL_MEMBER_ADD)),
    token: str | None = Depends(get_bearer_token),
    use_case: AddMemberToPoolUseCase = Depends(Provide[Container.add_member_to_pool_use_case]),
) -> UserResponse:
    """Add a member (resolved + mirrored from PM by external_id, caller's token)."""
    return UserResponse.from_mirror(await use_case.execute(pool_id, request.external_id, token))


@router.delete("/agent-pools/{pool_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def remove_member(
    pool_id: str,
    user_id: str,
    _: Principal = Depends(require_permission(POOL_MEMBER_REMOVE)),
    use_case: RemoveMemberFromPoolUseCase = Depends(Provide[Container.remove_member_from_pool_use_case]),
) -> None:
    use_case.execute(pool_id, user_id)
