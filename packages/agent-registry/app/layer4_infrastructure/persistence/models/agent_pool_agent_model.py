"""AgentPoolAgent ORM model - pool <-> agent M:N junction (SA RBAC v1).

Real FK with ON DELETE CASCADE on both sides (backstops a HARD delete). Note: an agent
is soft-deleted, so the cascade won't fire on agent soft-delete -> the agent soft_delete
path must clear these rows explicitly (T5).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class AgentPoolAgentModel(Base):
    """ORM model for the agent_pool_agents junction table."""

    __tablename__ = "agent_pool_agents"
    __table_args__ = (
        Index("ix_agent_pool_agents_agent_id", "agent_id"),
    )

    pool_id = Column(
        String(36),
        ForeignKey("agent_pools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_id = Column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "pool_id": self.pool_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
        }
