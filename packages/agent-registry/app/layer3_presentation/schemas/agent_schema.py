"""Pydantic schemas for Agent API endpoints."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer1_domain.entities.agent import AgentKind, ConfigType, AgentStatus


# ========== Request Schemas ==========

class RegisterAgentRequest(BaseModel):
    """Request schema for POST /agents (UC1: Register Agent)."""

    name: str = Field(..., min_length=1, max_length=255, description="Agent name")
    description: str = Field(..., description="Agent description")
    kind: str = Field(..., description="Agent kind: business or technical")
    status: str = Field(..., description="Agent status: active or inactive")
    config_type: str = Field(..., description="Configuration type: default or custom")
    business: str = Field(..., description="Business context / system prompt")
    tools: list[str] | None = Field(default=None, description="List of tool IDs to attach")
    workflows: list[str] | None = Field(default=None, description="List of workflow IDs to attach")
    agents: list[str] | None = Field(default=None, description="List of agent IDs to attach")
    knowledge_base: list[str] | None = Field(default=None, description="List of knowledge base document IDs to attach")
    is_published: bool = Field(default=False, description="Whether agent is published")
    version: str = Field(default="1.0.0", description="Agent version")
    healthcheck_endpoint: str | None = Field(None, description="Optional health check URL")
    invoke_endpoint: str | None = Field(None, description="Invocation URL")
    model: str | None = Field(None, description="LLM model to use (e.g. gpt-4, gpt-3.5)")
    provider: str | None = Field(None, description="LLM provider (e.g. openai, anthropic)")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="LLM temperature setting")
    max_tokens: int | None = Field(None, gt=0, description="Maximum number of tokens to generate")
    timeout_ms: int | None = Field(None, gt=0, description="Execution timeout in milliseconds")
    max_concurrency: int | None = Field(None, ge=1, description="Maximum concurrent executions")
    retry_count: int | None = Field(None, ge=0, description="Number of retry attempts")
    streaming_supported: bool | None = Field(None, description="Whether streaming responses are supported")
    metadata: dict[str, Any] | None = Field(None, description="Additional agent metadata")
    custom_system_prompt: str | None = Field(None, description="Optional custom system prompt")
    custom_instructions: list[str] | None = Field(None, description="Optional custom instructions")
    custom_restrictions: list[str] | None = Field(None, description="Optional custom restrictions")
    blocked_topics: list[str] | None = Field(None, description="Optional blocked topics")
    blocked_keywords: list[str] | None = Field(None, description="Optional blocked keywords")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        return v.strip()
    
    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v: str) -> str:
        return v.strip()
    
    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        """Validate using domain enum, return string."""
        v_lower = v.lower()
        # Leverage domain enum for validation
        valid_values = [e.value for e in AgentKind]
        if v_lower not in valid_values:
            raise ValueError(f"kind must be one of {valid_values}")
        return v_lower
    
    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        v_lower = v.lower()
        # Leverage domain enum for validation
        valid_values = [e.value for e in AgentStatus if e != AgentStatus.DELETED]
        if v_lower not in valid_values:
            raise ValueError(f"status must be one of {valid_values}")
        return v_lower
    
    @field_validator("config_type")
    @classmethod
    def validate_config_type(cls, v: str) -> str:
        v_lower = v.lower()
        # Leverage domain enum for validation
        valid_values = [e.value for e in ConfigType]
        if v_lower not in valid_values:
            raise ValueError(f"config_type must be one of {valid_values}")
        return v_lower
    
    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str | None) -> str | None:
        """SA-1938: the FE chooses the provider and the registry only stores it.

        No allowlist - the LLM gateway routes 60+ OpenAI-compatible providers, so a
        hardcoded set here would need a backend release per new provider. Only
        normalise (trim + lowercase) and reject a blank string.
        """
        if v is not None:
            v_normalised = v.strip().lower()
            if not v_normalised:
                raise ValueError("provider must not be blank")
            return v_normalised
        return v


class UpdateAgentRequest(BaseModel):
    """Request schema for PUT /agents/{agent_id} (UC2: Update Agent).
    
    Note: This endpoint does NOT update is_published or status.
    Use dedicated endpoints for those:
    - POST /agents/{agent_id}/activate
    - POST /agents/{agent_id}/deactivate
    - POST /agents/{agent_id}/publish
    - POST /agents/{agent_id}/unpublish
    """
    
    name: str | None = Field(None, min_length=1, max_length=255, description="Agent name")
    description: str | None = Field(None, description="Agent description")
    kind: str | None = Field(None, description="Agent kind: business or technical")
    is_published: bool | None = Field(None, description="Whether agent is published")
    config_type: str | None = Field(None, description="Configuration type: default or custom")
    business: str | None = Field(None, description="Business context / system prompt")
    tools: list[str] | None = Field(default=None, description="List of tool IDs to attach")
    workflows: list[str] | None = Field(default=None, description="List of workflow IDs to attach")
    agents: list[str] | None = Field(default=None, description="List of agent IDs to attach")
    knowledge_base: list[str] | None = Field(default=None, description="List of knowledge base document IDs to attach")
    version: str | None = Field(default="1.0.0", description="Agent version")
    healthcheck_endpoint: str | None = Field(None, description="Optional health check URL")
    invoke_endpoint: str | None = Field(None, description="Invocation URL")
    model: str | None = Field(None, description="LLM model to use (e.g. gpt-4, gpt-3.5)")
    provider: str | None = Field(None, description="LLM provider (e.g. openai, anthropic)")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="LLM temperature setting")
    custom_system_prompt: str | None = Field(None, description="Optional custom system prompt")
    custom_instructions: list[str] | None = Field(None, description="Optional custom instructions")
    custom_restrictions: list[str] | None = Field(None, description="Optional custom restrictions")
    blocked_topics: list[str] | None = Field(None, description="Optional blocked topics")
    blocked_keywords: list[str] | None = Field(None, description="Optional blocked keywords")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str | None) -> str | None:
        if v is not None:
            return v.strip()
        return v
    
    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v: str | None) -> str | None:
        if v is not None:
            return v.strip()
        return v
    
    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str | None) -> str | None:
        if v is not None:
            v_lower = v.lower()
            # Leverage domain enum for validation
            valid_values = [e.value for e in AgentKind]
            if v_lower not in valid_values:
                raise ValueError(f"kind must be one of {valid_values}")
            return v_lower
        return v
    
    @field_validator("config_type")
    @classmethod
    def validate_config_type(cls, v: str | None) -> str | None:
        if v is not None:
            v_lower = v.lower()
            # Leverage domain enum for validation
            valid_values = [e.value for e in ConfigType]
            if v_lower not in valid_values:
                raise ValueError(f"config_type must be one of {valid_values}")
            return v_lower
        return v
    
    model_config = {"extra": "forbid"}
    
    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str | None) -> str | None:
        """SA-1938: the FE chooses the provider and the registry only stores it.

        No allowlist - the LLM gateway routes 60+ OpenAI-compatible providers, so a
        hardcoded set here would need a backend release per new provider. Only
        normalise (trim + lowercase) and reject a blank string.
        """
        if v is not None:
            v_normalised = v.strip().lower()
            if not v_normalised:
                raise ValueError("provider must not be blank")
            return v_normalised
        return v


class UpdateAgentStatusRequest(BaseModel):
    """Request schema for PATCH /agents/{agent_id}/status."""
    
    status: str = Field(..., description="New status: ACTIVE or INACTIVE")
    
    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        v_lower = v.lower()
        # Leverage domain enum for validation
        valid_values = [e.value for e in AgentStatus if e != AgentStatus.DELETED]
        if v_lower not in valid_values:
            raise ValueError(f"status must be one of {valid_values}")
        return v_lower


class AgentQueryParams(BaseModel):
    """Query parameters for GET /agents (UC4: Get Agents by Criteria)."""
    
    kind: str | None = Field(None, description="Filter by kind: BUSINESS or TECHNICAL")
    status: str | None = Field(None, description="Filter by status")
    is_published: bool | None = Field(None, description="Filter by publication status")
    
    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_upper = v.upper()
        if v_upper not in ["BUSINESS", "TECHNICAL"]:
            raise ValueError("kind must be BUSINESS or TECHNICAL")
        # Return lowercase for domain enum
        return v_upper.lower()
    
    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        v_lower = v.lower()
        # Leverage domain enum for validation
        valid_values = [e.value for e in AgentStatus if e != AgentStatus.DELETED]
        if v_lower not in valid_values:
            raise ValueError(f"status must be one of {valid_values}")
        return v_lower


# ========== Response Schemas ==========

class AgentResponse(BaseModel):
    """Response schema for single agent."""
    
    id: str
    name: str
    kind: str
    status: str
    is_published: bool
    description: str | None = None
    healthcheck_endpoint: str | None = None
    invoke_endpoint: str | None = None
    is_alive: bool | None = None
    last_health_check_at: datetime | None = None
    version: str
    config_type: str
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    capabilities: list[str] | None = None
    capability_summary: str | None = None
    agents: list[str] | None = None
    workflows: list[str] | None = None
    tools: list[str] | None = None
    knowledge_base: list[str] | None = None
    metadata: dict[str, Any] | None = None
    business: str | None = None
    user_email: str | None = None
    tenant_id: str | None = None
    custom_system_prompt: str | None = None
    custom_instructions: list[str] | None = None
    custom_restrictions: list[str] | None = None
    blocked_topics: list[str] | None = None
    blocked_keywords: list[str] | None = None
    user_roles: list[str] | None = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}
    
    @classmethod
    def from_dto(cls, dto: AgentResponseDTO) -> "AgentResponse":
        """Create AgentResponse from AgentResponseDTO."""
        return cls(
            id=dto.id,
            name=dto.name,
            kind=dto.kind,
            status=dto.status,
            is_published=dto.is_published,
            description=dto.description,
            healthcheck_endpoint=dto.healthcheck_endpoint,
            invoke_endpoint=dto.invoke_endpoint,
            is_alive=dto.is_alive,
            last_health_check_at=dto.last_health_check_at,
            version=dto.version,
            config_type=dto.config_type,
            provider=dto.provider,
            model=dto.model,
            temperature=dto.temperature,
            capabilities=dto.capabilities,
            capability_summary=dto.capability_summary,
            metadata=dto.metadata,
            tools=dto.tools,
            workflows=dto.workflows,
            agents=dto.agents,
            knowledge_base=dto.knowledge_base,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
            business=dto.business,
            user_email=dto.user_email,
            tenant_id=dto.tenant_id,
            custom_system_prompt=dto.custom_system_prompt,
            custom_instructions=dto.custom_instructions,
            custom_restrictions=dto.custom_restrictions,
            blocked_topics=dto.blocked_topics,
            blocked_keywords=dto.blocked_keywords,
            user_roles=dto.user_roles,
        )
    

class AgentSummaryResponse(BaseModel):
    """Response schema for agent summary info (used in list endpoints)."""
    
    id: str
    name: str
    kind: str
    status: str
    is_published: bool
    description: str | None = None
    version: str
    config_type: str
    capabilities: list[str] | None = None
    capability_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_dto(cls, dto: AgentResponseDTO) -> "AgentSummaryResponse":
        """Create AgentSummaryResponse from AgentResponseDTO."""
        return cls(
            id=dto.id,
            name=dto.name,
            kind=dto.kind,
            status=dto.status,
            is_published=dto.is_published,
            description=dto.description,
            version=dto.version,
            config_type=dto.config_type,
            capabilities=dto.capabilities,
            capability_summary=dto.capability_summary,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
        )


class AgentListResponse(BaseModel):
    """Response schema for list of agents."""
    
    agents: list[AgentResponse]
    total: int
    
    
class AgentSummaryListResponse(BaseModel):
    """Response schema for list of agents with summary info."""
    
    agents: list[AgentSummaryResponse]
    total: int


class DeleteAgentResponse(BaseModel):
    """Response schema for DELETE /agents/{agent_id}."""
    
    message: str
    deleted_agent_id: str


class ErrorResponse(BaseModel):
    """Standard error response schema."""
    
    error: str
    detail: str | None = None
    status_code: int
