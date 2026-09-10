"""AgentTool ORM model - Junction table for Agent-Tool M:N relationship."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.layer4_infrastructure.persistence.models.base_model import Base


class AgentToolModel(Base):
    """Junction table for Agent-Tool many-to-many relationship."""

    __tablename__ = "agent_tools"
    __table_args__ = (
        Index("ix_agent_tool_unique", "agent_id", "tool_id", unique=True),
    )

    # Primary key
    id = Column(String(36), primary_key=True)

    # Foreign keys
    agent_id = Column(
        String(36),
        ForeignKey("agents.id"),
        nullable=False,
        index=True,
    )
    tool_id = Column(
        String(36),
        ForeignKey("tools.id"),
        nullable=False,
        index=True,
    )

    # Timestamp
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "tool_id": self.tool_id,
            "created_at": self.created_at,
        }
