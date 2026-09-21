"""UC: create a custom role (SA RBAC v1, Task 3)."""

from app.layer1_domain.exceptions import AlreadyExistsException, InvalidDataException
from app.layer2_application.dtos.rbac_read_models import RoleDetail
from app.layer2_application.interfaces.role_repository_port import IRoleRepository


class CreateRoleUseCase:
    """Create a custom role (is_system=False, is_super=False). Guarded by `role.create`."""

    def __init__(self, repository: IRoleRepository):
        self.repository = repository

    def execute(self, code: str, name: str, description: str | None = None) -> RoleDetail:
        code = (code or "").strip()
        name = (name or "").strip()
        if not code:
            raise InvalidDataException("Role code is required", field="code")
        if not name:
            raise InvalidDataException("Role name is required", field="name")
        if self.repository.get_role_by_code(code) is not None:
            raise AlreadyExistsException("Role", code)
        return self.repository.create_role(code, name, description)
