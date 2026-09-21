"""Users + principal router (SA RBAC v1, Task 3).

`GET /me` (any principal), `GET /users` (`user.read`), and
`PUT /users/{external_id}/role` (assign-role — the required permission is computed from
the transition inside the use case, so the route is guarded by authentication only).
"""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from app.layer1_domain.entities.principal import Principal
from app.layer2_application.use_cases.assign_role import AssignRoleUseCase
from app.layer2_application.use_cases.list_users import ListUsersUseCase
from app.layer3_presentation.dependencies.principal import (
    get_bearer_token,
    get_current_principal,
    require_permission,
)
from app.layer3_presentation.schemas.principal_schema import MeResponse
from app.layer3_presentation.schemas.rbac_user_schema import (
    AssignRoleRequest,
    UserListResponse,
    UserResponse,
)
from container import Container

router = APIRouter(prefix="/api/v1/agents-registry", tags=["users"])


@router.get(
    "/me",
    response_model=MeResponse,
    responses={200: {"description": "The caller's resolved principal"}},
)
async def get_me(
    principal: Principal = Depends(get_current_principal),
) -> MeResponse:
    """Return the caller's resolved principal (role, permissions, is_super)."""
    return MeResponse.from_principal(principal)


@router.get("/users", response_model=UserListResponse)
@inject
async def list_users(
    _: Principal = Depends(require_permission("user.read")),
    use_case: ListUsersUseCase = Depends(Provide[Container.list_users_use_case]),
) -> UserListResponse:
    """List the RBAC user mirror + each user's role."""
    users = use_case.execute()
    return UserListResponse(users=[UserResponse.from_mirror(u) for u in users], total=len(users))


@router.put("/users/{external_id}/role", response_model=UserResponse)
@inject
async def assign_role(
    external_id: str,
    request: AssignRoleRequest,
    principal: Principal = Depends(get_current_principal),
    token: str | None = Depends(get_bearer_token),
    use_case: AssignRoleUseCase = Depends(Provide[Container.assign_role_use_case]),
) -> UserResponse:
    """Assign a role to a user. The required permission is derived from the transition
    (admin / super_admin / custom tier), with the R3 last-super_admin lockout."""
    updated = await use_case.execute(
        caller=principal, external_id=external_id, new_role_id=request.role_id, token=token
    )
    return UserResponse.from_mirror(updated)
