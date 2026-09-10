"""UC: list roles + their permission codes (SA RBAC v1, Task 3)."""

from app.layer2_application.dtos.rbac_read_models import RoleDetail
from app.layer2_application.interfaces.role_repository_port import IRoleRepository


class ListRolesUseCase:
    """Return all roles with their permission sets (guarded by `role.read`)."""

    def __init__(self, repository: IRoleRepository):
        self.repository = repository

    def execute(self) -> list[RoleDetail]:
        return self.repository.list_roles()
