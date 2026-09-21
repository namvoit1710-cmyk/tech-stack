"""Permission ORM model - app-defined permission catalog (SA RBAC v1).

The catalog is seeded by the T1 migration and is app-defined: the config UI maps
existing codes to roles but never creates new codes. Guards check permission codes,
not role names.
"""

from sqlalchemy import Column, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class PermissionModel(Base):
    """ORM model for the permissions table - the app permission catalog."""

    __tablename__ = "permissions"
    __table_args__ = (
        Index("ix_permissions_code", "code", unique=True),
    )

    # Primary key (application-generated UUIDv7)
    id = Column(String(36), primary_key=True)

    # Stable machine code (e.g. agent.read, pool.create, role.update_mapping_permission)
    code = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "id": self.id,
            "code": self.code,
            "description": self.description,
        }
