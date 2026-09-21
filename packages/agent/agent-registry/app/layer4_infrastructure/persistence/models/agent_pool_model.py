"""AgentPool ORM model - groups agents and users (SA RBAC v1).

A pool contains agents (agent_pool_agents) and members (agent_pool_members), both M:N.
Visibility: a user without agent.view_all sees only agents in pools they belong to.

Aggregate root -> SOFT delete (status 'active'|'deleted'), mirroring the Agent/Tool
pattern. Because FK ON DELETE CASCADE never fires on a soft delete (the row stays),
pool soft-delete MUST explicitly clear its agent_pool_agents/agent_pool_members rows
(handled in the pool use case, T4). Every pool read filters status != 'deleted'.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class AgentPoolModel(Base):
    """ORM model for the agent_pools table."""

    __tablename__ = "agent_pools"

    # Primary key (application-generated UUIDv7)
    id = Column(String(36), primary_key=True)

    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)

    # Soft-delete flag: 'active' | 'deleted'
    status = Column(String(20), nullable=False, default="active")

    # Creator (internal users.id). SET NULL so deleting a user doesn't delete their pools.
    created_by = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

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
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
