"""RolePermission ORM model - role <-> permission M:N mapping (SA RBAC v1).

Editable on the config UI (super_admin only). Real foreign keys with ON DELETE
CASCADE on both sides: deleting a role or a permission removes its mapping rows.
NOTE: real FK diverges from the repo's app-level-relationship convention (see PR note).
"""

from sqlalchemy import Column, ForeignKey, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class RolePermissionModel(Base):
    """ORM model for the role_permissions junction table."""

    __tablename__ = "role_permissions"

    role_id = Column(
        String(36),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id = Column(
        String(36),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "role_id": self.role_id,
            "permission_id": self.permission_id,
        }
