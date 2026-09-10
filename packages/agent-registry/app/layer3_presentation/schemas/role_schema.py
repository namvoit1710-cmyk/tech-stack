"""Role + permission API schemas (SA RBAC v1, Task 3)."""

from pydantic import BaseModel, Field

from app.layer2_application.dtos.rbac_read_models import PermissionCatalogItem, RoleDetail


class RoleResponse(BaseModel):
    """A role with its granted permission codes."""

    id: str
    code: str
    name: str
    description: str | None = None
    is_system: bool
    is_super: bool
    permissions: list[str] = []

    @classmethod
    def from_detail(cls, d: RoleDetail) -> "RoleResponse":
        return cls(
            id=d.id, code=d.code, name=d.name, description=d.description,
            is_system=d.is_system, is_super=d.is_super, permissions=list(d.permission_codes),
        )


class RoleListResponse(BaseModel):
    roles: list[RoleResponse]
    total: int


class CreateRoleRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)


class UpdateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)


class SetRolePermissionsRequest(BaseModel):
    codes: list[str] = Field(default_factory=list)


class PermissionResponse(BaseModel):
    id: str
    code: str
    description: str | None = None

    @classmethod
    def from_item(cls, p: PermissionCatalogItem) -> "PermissionResponse":
        return cls(id=p.id, code=p.code, description=p.description)


class PermissionListResponse(BaseModel):
    permissions: list[PermissionResponse]
    total: int
