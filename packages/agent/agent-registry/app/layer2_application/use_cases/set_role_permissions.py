"""UC: set a role's permission mapping (SA RBAC v1, Task 3)."""

from app.layer1_domain.exceptions import InvalidDataException, InvalidOperationException, NotFoundException
from app.layer2_application.dtos.rbac_read_models import RoleDetail
from app.layer2_application.interfaces.permission_repository_port import IPermissionRepository
from app.layer2_application.interfaces.role_repository_port import IRoleRepository


class SetRolePermissionsUseCase:
    """Replace a role's permission set. Guarded by `role.update_mapping_permission`.

    The `is_super` role is LOCKED (always all permissions) → rejected. `admin`/`user`
    (is_system) and custom roles are editable. Codes must exist in the app catalog
    (the UI never invents codes).
    """

    def __init__(self, role_repository: IRoleRepository, permission_repository: IPermissionRepository):
        self.role_repository = role_repository
        self.permission_repository = permission_repository

    def execute(self, role_id: str, codes: list[str]) -> RoleDetail:
        role = self.role_repository.get_role(role_id)
        if role is None:
            raise NotFoundException("Role", entity_id=role_id)
        if role.is_super:
            raise InvalidOperationException(
                "The super_admin role is locked (always all permissions) and cannot be edited"
            )

        requested = list(dict.fromkeys(codes or []))  # de-dup, keep order
        code_to_id = self.permission_repository.find_ids_for_codes(requested)
        unknown = [c for c in requested if c not in code_to_id]
        if unknown:
            raise InvalidDataException(f"Unknown permission code(s): {', '.join(unknown)}")

        self.role_repository.set_role_permissions(role_id, [code_to_id[c] for c in requested])
        return self.role_repository.get_role(role_id)
