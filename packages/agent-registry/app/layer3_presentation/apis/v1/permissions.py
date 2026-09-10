"""Permissions router (SA RBAC v1, Task 3)."""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from app.layer1_domain.entities.principal import Principal
from app.layer2_application.use_cases.list_permissions import ListPermissionsUseCase
from app.layer3_presentation.dependencies.principal import require_permission
from app.layer3_presentation.schemas.role_schema import PermissionListResponse, PermissionResponse
from container import Container

router = APIRouter(prefix="/api/v1/agents-registry", tags=["permissions"])


@router.get("/permissions", response_model=PermissionListResponse)
@inject
async def list_permissions(
    _: Principal = Depends(require_permission("permission.view")),
    use_case: ListPermissionsUseCase = Depends(Provide[Container.list_permissions_use_case]),
) -> PermissionListResponse:
    """List the app-defined permission catalog (super_admin only)."""
    items = use_case.execute()
    return PermissionListResponse(
        permissions=[PermissionResponse.from_item(p) for p in items],
        total=len(items),
    )
