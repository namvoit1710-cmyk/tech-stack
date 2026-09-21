"""Principal / `/me` response schemas (SA RBAC v1, Task 3)."""

from pydantic import BaseModel

from app.layer1_domain.entities.principal import Principal


class MeResponse(BaseModel):
    """The caller's resolved principal.

    NB: for an `is_super` (or `system`) principal, `permissions` is intentionally empty
    — authorization goes through the `is_super` bypass, so clients must treat
    `is_super=true` as "all permissions" rather than reading the `permissions` list.
    """

    kind: str
    user_id: str | None = None
    external_id: str | None = None
    role_code: str | None = None
    is_super: bool = False
    permissions: list[str] = []

    @classmethod
    def from_principal(cls, principal: Principal) -> "MeResponse":
        return cls(
            kind=principal.kind,
            user_id=principal.user_id,
            external_id=principal.external_id,
            role_code=principal.role_code,
            is_super=principal.is_super,
            permissions=sorted(principal.permissions),
        )
