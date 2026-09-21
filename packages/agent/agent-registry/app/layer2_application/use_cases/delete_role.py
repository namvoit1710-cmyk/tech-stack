"""UC: delete a custom role (SA RBAC v1, Task 3)."""

from app.layer1_domain.exceptions import ConflictException, InvalidOperationException, NotFoundException
from app.layer2_application.interfaces.role_repository_port import IRoleRepository


class DeleteRoleUseCase:
    """Delete a custom role. Guarded by `role.remove`.

    Built-in (`is_system`) roles cannot be deleted. A custom role that is still assigned
    to any user cannot be deleted (409).
    """

    def __init__(self, repository: IRoleRepository):
        self.repository = repository

    def execute(self, role_id: str) -> None:
        role = self.repository.get_role(role_id)
        if role is None:
            raise NotFoundException("Role", entity_id=role_id)
        if role.is_system:
            raise InvalidOperationException(
                f"Built-in role '{role.code}' cannot be deleted"
            )
        if self.repository.is_role_in_use(role_id):
            raise ConflictException(
                f"Role '{role.code}' is still assigned to one or more users"
            )
        self.repository.delete_role(role_id)
