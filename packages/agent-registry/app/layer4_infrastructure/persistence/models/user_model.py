"""User ORM model - RBAC user mirror, keyed on external_id (SA-1553 + RBAC v1).

Populated lazily from Profile Management (PM) keyed on a stable external_id (IAS/PM id).
email/name are cosmetic (refreshed on touch, never used for authz). Each user has one
role via role_id (dynamic RBAC — no account_role enum). external_id is the join key, so
email is NO LONGER unique. tenant_id is kept nullable/unused (tenant deferred).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class UserModel(Base):
    """A user the registry has seen (mirrored from PM by external_id)."""

    __tablename__ = "users"
    __table_args__ = (
        # external_id is the stable identity/join key (unique).
        Index("ix_users_external_id", "external_id", unique=True),
        # email loses its UNIQUE (external_id is the key); kept as a plain lookup index.
        Index("ix_users_email", "email", unique=False),
    )

    # Primary key (application-generated UUID)
    id = Column(String(36), primary_key=True)

    # Stable IAS/PM identity (unique). Nullable at DB level for legacy rows; app treats
    # it as required going forward (backfill handled at the app layer).
    external_id = Column(String(255), nullable=True)

    # Nullable since the T5 contract migration (20260716_02): external_id is the key and
    # email is cosmetic, so a PM-without-email user stores NULL (not a "" placeholder).
    email = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)

    # One role per user (dynamic RBAC). Nullable at add-time; backfilled to the 'user'
    # role by the T1 migration, then the app treats it as required.
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=True)

    tenant_id = Column(String(36), nullable=True, index=True)

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
            "external_id": self.external_id,
            "email": self.email,
            "name": self.name,
            "role_id": self.role_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
