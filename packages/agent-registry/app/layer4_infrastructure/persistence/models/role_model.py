"""Role ORM model - dynamic RBAC role owned by the Agent Registry (SA RBAC v1).

A role groups a set of permissions (via role_permissions). Each user has exactly one
role (users.role_id). Built-in roles are seeded by the T1 migration:
  - super_admin (is_super, is_system): always ALL permissions, locked, bypasses checks.
  - admin       (is_system):           editable permission set.
  - user        (is_system):           editable, default for new users.
Custom roles (is_system=False) are created on the later config UI (super_admin only).
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class RoleModel(Base):
    """ORM model for the roles table - dynamic RBAC role."""

    __tablename__ = "roles"
    __table_args__ = (
        Index("ix_roles_code", "code", unique=True),
    )

    # Primary key (application-generated UUIDv7)
    id = Column(String(36), primary_key=True)

    # Stable machine code (e.g. super_admin, admin, user, <custom>)
    code = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)

    # Built-in roles cannot be renamed/deleted; is_super bypasses all permission checks
    # and its permission set is locked (always all).
    is_system = Column(Boolean, nullable=False, default=False)
    is_super = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "is_system": self.is_system,
            "is_super": self.is_super,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
