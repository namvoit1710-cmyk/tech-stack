"""AgentPoolMember ORM model - pool <-> user M:N junction (SA RBAC v1).

Real FK with ON DELETE CASCADE on both sides. A user hard-deleted removes their
memberships; a pool hard-deleted removes its member rows. On pool SOFT delete the
cascade won't fire -> the pool use case clears these rows explicitly (T4).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class AgentPoolMemberModel(Base):
    """ORM model for the agent_pool_members junction table."""

    __tablename__ = "agent_pool_members"
    __table_args__ = (
        Index("ix_agent_pool_members_user_id", "user_id"),
    )

    pool_id = Column(
        String(36),
        ForeignKey("agent_pools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "pool_id": self.pool_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
        }
