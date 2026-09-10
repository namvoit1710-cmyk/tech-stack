"""Agent ORM model - SQLAlchemy model for Agent table."""

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, UniqueConstraint, Index, Integer, String, Text

from app.layer4_infrastructure.persistence.models.base_model import Base

class AgentModel(Base):
    """ORM model for Agent table - infrastructure concern.
    
    This is separate from the domain Agent entity.
    The repository handles translation between AgentModel and Agent entity.
    """

    __tablename__ = "agents"
    # ix_agents_kind_status: Index on kind and status columns for faster queries filtering by these fields.
    # ix_agents_is_alive_kind: Index on is_alive and kind columns for faster queries filtering by these fields.
    __table_args__ = (
        Index("ix_agents_kind_status", "kind", "status"),
        Index("ix_agents_is_alive_kind", "is_alive", "kind"),
        UniqueConstraint("name", "version", name="uq_agents_name_version"),
    )

    # Primary key
    id = Column(String(36), primary_key=True)

    # Core fields
    name = Column(String(255), nullable=False)
    kind = Column(String(20), nullable=False, index=True)  # business, technical
    status = Column(
        String(20), nullable=False, index=True
    )  # active, inactive
    is_published = Column(Boolean, nullable=False, default=False, index=True)

    # Descriptive fields
    description = Column(Text, nullable=True)

    # Endpoints
    healthcheck_endpoint = Column(String(500), nullable=True)
    invoke_endpoint = Column(String(500), nullable=True)

    # Health status
    is_alive = Column(Boolean, nullable=False, default=False, index=True)
    last_health_check_at = Column(DateTime, nullable=True)

    # Version and metadata
    version = Column(String(50), nullable=False, default="1.0.0")
    agent_metadata = Column(Text, nullable=True)  # JSON string (renamed from metadata)

    # Runtime config fields (merged from AgentRuntimeConfig)
    provider = Column(String(50), nullable=True)  # LLM provider; NULL = not chosen
    model = Column(String(255), nullable=True)  # Model identifier; NULL = not chosen
    temperature = Column(Float, nullable=True)  # Sampling temperature (0.0-2.0); NULL = not chosen
    max_tokens = Column(Integer, nullable=False)  # Maximum tokens (>0)
    system_prompt = Column(Text, nullable=True)  # System prompt for LLM
    config_type = Column(String(20), nullable=False, default="default")  # Configuration type (default, custom)

    # Execution policy fields (merged from ExecutionPolicy)
    timeout_ms = Column(Integer, nullable=False)  # Timeout in milliseconds
    max_concurrency = Column(Integer, nullable=False)  # Max concurrent executions
    retry_count = Column(Integer, nullable=False)  # Number of retry attempts
    streaming_supported = Column(Boolean, nullable=False)  # Whether streaming is supported

    # Capabilities and relationships
    capabilities = Column(Text, nullable=True)  # JSON array of capability names/intents
    capability_summary = Column(Text, nullable=True)  # Concise, deduplicated, human-readable capability summary
    attached_agent_ids = Column(Text, nullable=True)  # JSON array of agent IDs
    knowledge_base = Column(Text, nullable=True)  # JSON array of document IDs
    
    # Custom fields
    custom_system_prompt = Column(Text, nullable=True)
    custom_instructions = Column(Text, nullable=True)  # JSON array
    custom_restrictions = Column(Text, nullable=True)  # JSON array
    blocked_topics = Column(Text, nullable=True)  # JSON array
    blocked_keywords = Column(Text, nullable=True)  # JSON array

    # Access control
    user_roles = Column(Text, nullable=True)  # JSON array of user user_roles

    # User and tenant info for multi-tenancy and auditing
    user_email = Column(String(255), nullable=True, index=True)  # Email of the user who registered the agent
    tenant_id = Column(String(36), nullable=True, index=True)  # ID of the tenant

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert ORM model to dictionary.
        
        Returns:
            Dictionary representation of the model
        """
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "is_published": self.is_published,
            "description": self.description,
            "healthcheck_endpoint": self.healthcheck_endpoint,
            "invoke_endpoint": self.invoke_endpoint,
            "is_alive": self.is_alive,
            "last_health_check_at": self.last_health_check_at,
            "version": self.version,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system_prompt": self.system_prompt,
            "config_type": self.config_type,
            "timeout_ms": self.timeout_ms,
            "max_concurrency": self.max_concurrency,
            "retry_count": self.retry_count,
            "streaming_supported": self.streaming_supported,
            "capabilities": json.loads(self.capabilities) if self.capabilities else [],
            "capability_summary": self.capability_summary or "",
            "attached_agent_ids": json.loads(self.attached_agent_ids)
            if self.attached_agent_ids
            else [],
            "knowledge_base": json.loads(self.knowledge_base) if self.knowledge_base else [],
            "custom_system_prompt": self.custom_system_prompt,
            "custom_instructions": json.loads(self.custom_instructions) if self.custom_instructions else [],
            "custom_restrictions": json.loads(self.custom_restrictions) if self.custom_restrictions else [],
            "blocked_topics": json.loads(self.blocked_topics) if self.blocked_topics else [],
            "blocked_keywords": json.loads(self.blocked_keywords) if self.blocked_keywords else [],
            "user_roles": json.loads(self.user_roles) if self.user_roles else [],
            "metadata": json.loads(self.agent_metadata) if self.agent_metadata else {},
            "user_email": self.user_email,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
