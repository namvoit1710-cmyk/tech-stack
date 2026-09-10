"""Workflow ORM model - SQLAlchemy model for Workflow table."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text

from app.layer4_infrastructure.persistence.models.base_model import Base


class WorkflowModel(Base):
    """ORM model for Workflow table - infrastructure concern."""

    __tablename__ = "workflows"

    # Primary key
    id = Column(String(36), primary_key=True)

    # Core fields
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    version = Column(String(50), nullable=False, index=True)
    workflow_metadata = Column("metadata", Text, nullable=True)
    output_schema = Column(Text, nullable=True)
    input_schema = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, index=True)
    main_flow = Column(Boolean, nullable=False, default=False, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "status": self.status,
            "main_flow": self.main_flow,
            "metadata": self.workflow_metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }