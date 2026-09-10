"""Roles router — CRUD + permission mapping (SA RBAC v1, Task 3)."""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status

from app.layer1_domain.entities.principal import Principal
from app.layer2_application.use_cases.create_role import CreateRoleUseCase
from app.layer2_application.use_cases.delete_role import DeleteRoleUseCase
from app.layer2_application.use_cases.list_roles import ListRolesUseCase
from app.layer2_application.use_cases.set_role_permissions import SetRolePermissionsUseCase
from app.layer2_application.use_cases.update_role import UpdateRoleUseCase
from app.layer3_presentation.dependencies.principal import require_permission
from app.layer3_presentation.schemas.role_schema import (
    CreateRoleRequest,
    RoleListResponse,
    RoleResponse,
    SetRolePermissionsRequest,
    UpdateRoleRequest,
)
from container import Container

router = APIRouter(prefix="/api/v1/agents-registry", tags=["roles"])


@router.get("/roles", response_model=RoleListResponse)
@inject
async def list_roles(
    _: Principal = Depends(require_permission("role.read")),
    use_case: ListRolesUseCase = Depends(Provide[Container.list_roles_use_case]),
) -> RoleListResponse:
    """List all roles + their permission codes."""
    roles = use_case.execute()
    return RoleListResponse(roles=[RoleResponse.from_detail(r) for r in roles], total=len(roles))


@router.post("/roles", status_code=status.HTTP_201_CREATED, response_model=RoleResponse)
@inject
async def create_role(
    request: CreateRoleRequest,
    _: Principal = Depends(require_permission("role.create")),
    use_case: CreateRoleUseCase = Depends(Provide[Container.create_role_use_case]),
) -> RoleResponse:
    """Create a custom role (super_admin only)."""
    return RoleResponse.from_detail(
        use_case.execute(code=request.code, name=request.name, description=request.description)
    )


@router.put("/roles/{role_id}", response_model=RoleResponse)
@inject
async def update_role(
    role_id: str,
    request: UpdateRoleRequest,
    _: Principal = Depends(require_permission("role.update")),
    use_case: UpdateRoleUseCase = Depends(Provide[Container.update_role_use_case]),
) -> RoleResponse:
    """Update a role's name/description (is_system roles: description only)."""
    return RoleResponse.from_detail(
        use_case.execute(role_id=role_id, name=request.name, description=request.description)
    )


@router.put("/roles/{role_id}/permissions", response_model=RoleResponse)
@inject
async def set_role_permissions(
    role_id: str,
    request: SetRolePermissionsRequest,
    _: Principal = Depends(require_permission("role.update_mapping_permission")),
    use_case: SetRolePermissionsUseCase = Depends(Provide[Container.set_role_permissions_use_case]),
) -> RoleResponse:
    """Replace a role's permission set (rejected for the locked super_admin role)."""
    return RoleResponse.from_detail(use_case.execute(role_id=role_id, codes=request.codes))


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_role(
    role_id: str,
    _: Principal = Depends(require_permission("role.remove")),
    use_case: DeleteRoleUseCase = Depends(Provide[Container.delete_role_use_case]),
) -> None:
    """Delete a custom role (built-in blocked; in-use → 409)."""
    use_case.execute(role_id)
