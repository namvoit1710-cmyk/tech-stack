"""UC: update a role's name/description (SA RBAC v1, Task 3)."""

from app.layer1_domain.exceptions import InvalidOperationException, NotFoundException
from app.layer2_application.dtos.rbac_read_models import RoleDetail
from app.layer2_application.interfaces.role_repository_port import IRoleRepository


class UpdateRoleUseCase:
    """Update a role. Guarded by `role.update`.

    Built-in (`is_system`) roles — including `super_admin` — may only have their
    description changed; their identity (name/code) is fixed. Custom roles are fully
    editable. (Code is never changed via this endpoint.)
    """

    def __init__(self, repository: IRoleRepository):
        self.repository = repository

    def execute(self, role_id: str, name: str, description: str | None = None) -> RoleDetail:
        role = self.repository.get_role(role_id)
        if role is None:
            raise NotFoundException("Role", entity_id=role_id)

        name = (name or "").strip()
        if not name:
            raise InvalidOperationException("Role name cannot be empty")

        if role.is_system and name != role.name:
            raise InvalidOperationException(
                f"Built-in role '{role.code}' cannot be renamed (only its description is editable)"
            )

        self.repository.update_role(role_id, name, description)
        return self.repository.get_role(role_id)
