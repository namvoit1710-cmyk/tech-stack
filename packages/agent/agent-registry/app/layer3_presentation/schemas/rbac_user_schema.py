"""RBAC user (mirror) API schemas (SA RBAC v1, Task 3)."""

from pydantic import BaseModel, Field

from app.layer2_application.dtos.rbac_read_models import MirrorUser


class UserResponse(BaseModel):
    """A mirrored user joined with their role."""

    user_id: str
    external_id: str | None = None
    email: str | None = None
    name: str | None = None
    role_id: str | None = None
    role_code: str | None = None
    is_super: bool = False

    @classmethod
    def from_mirror(cls, u: MirrorUser) -> "UserResponse":
        return cls(
            user_id=u.user_id, external_id=u.external_id, email=u.email, name=u.name,
            role_id=u.role_id, role_code=u.role_code, is_super=u.is_super,
        )


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int


class AssignRoleRequest(BaseModel):
    role_id: str = Field(min_length=1)
