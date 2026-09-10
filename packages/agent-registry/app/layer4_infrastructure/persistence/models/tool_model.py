"""Tool ORM model - SQLAlchemy model for Tool table."""

import json
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text

from app.layer4_infrastructure.persistence.models.base_model import Base


class ToolModel(Base):
    """ORM model for Tool table - infrastructure concern."""

    __tablename__ = "tools"

    # Primary key
    id = Column(String(36), primary_key=True)

    # Core fields
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    protocol = Column(String(50), nullable=False, index=True)  # rest, grpc, mcp, websocket
    endpoint = Column(String(500), nullable=True)
    parameters_schema = Column(Text, nullable=True)  # JSON string
    response_schema = Column(Text, nullable=True)  # JSON string
    auth_config = Column(Text, nullable=True)  # JSON string
    version = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, index=True)  # active, inactive, deleted

    # Metadata
    entity_metadata = Column(Text, nullable=True)  # JSON string

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
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "parameters_schema": json.loads(self.parameters_schema)
            if self.parameters_schema
            else {},
            "response_schema": json.loads(self.response_schema)
            if self.response_schema
            else {},
            "auth_config": json.loads(self.auth_config) if self.auth_config else {},
            "version": self.version,
            "status": self.status,
            "metadata": json.loads(self.entity_metadata) if self.entity_metadata else {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
