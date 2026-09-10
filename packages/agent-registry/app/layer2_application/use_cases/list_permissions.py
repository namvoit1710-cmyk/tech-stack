"""UC: list the app permission catalog (SA RBAC v1, Task 3)."""

from app.layer2_application.dtos.rbac_read_models import PermissionCatalogItem
from app.layer2_application.interfaces.permission_repository_port import IPermissionRepository


class ListPermissionsUseCase:
    """Return the full seeded permission catalog (guarded by `permission.view`)."""

    def __init__(self, repository: IPermissionRepository):
        self.repository = repository

    def execute(self) -> list[PermissionCatalogItem]:
        return self.repository.list_permissions()
