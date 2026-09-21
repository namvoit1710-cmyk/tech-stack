"""Role repository port (SA RBAC v1, Task 3)."""

from typing import Protocol

from app.layer2_application.dtos.rbac_read_models import RoleDetail


class IRoleRepository(Protocol):
    """Persistence contract for roles + their permission mappings."""

    def list_roles(self) -> list[RoleDetail]:
        """All roles, each with its granted permission codes."""
        ...

    def get_role(self, role_id: str) -> RoleDetail | None:
        """A role by id (with permission codes), or None."""
        ...

    def get_role_by_code(self, code: str) -> RoleDetail | None:
        """A role by its stable code (for uniqueness checks), or None."""
        ...

    def create_role(self, code: str, name: str, description: str | None) -> RoleDetail:
        """Create a custom role (is_system=False, is_super=False)."""
        ...

    def update_role(self, role_id: str, name: str, description: str | None) -> None:
        """Update a role's name/description."""
        ...

    def set_role_permissions(self, role_id: str, permission_ids: list[str]) -> None:
        """Replace a role's permission set with the given permission ids."""
        ...

    def delete_role(self, role_id: str) -> None:
        """Hard-delete a custom role (cascade clears its role_permissions)."""
        ...

    def is_role_in_use(self, role_id: str) -> bool:
        """True if any user currently holds this role."""
        ...
