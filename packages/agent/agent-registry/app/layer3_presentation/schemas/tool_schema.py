"""Pydantic schemas for Tool API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO


# ========== Request Schemas ==========

class RegisterToolRequest(BaseModel):
    """Request schema for POST /tools (UC-RegisterTool)."""
    
    name: str = Field(..., min_length=1, max_length=255, description="Tool name")
    description: str = Field(..., description="Tool description")
    protocol: str = Field(..., description="Tool protocol: rest, grpc, mcp, websocket")
    endpoint: str | None = Field(default=None, description="Tool endpoint URL when the tool exposes one")
    version: str = Field(default="1.0.0", description="Tool version")
    status: str = Field(default="active", description="Tool status: active, inactive")
    input_data: dict[str, Any] | None = Field(
        default=None,
        description="JSON schema for tool input (OpenAPI 3.0 compatible)"
    )
    output_data: dict[str, Any] | None = Field(
        default=None,
        description="JSON schema for tool output (OpenAPI 3.0 compatible)"
    )
    auth_config: dict[str, Any] | None = Field(
        default=None,
        description="Authentication configuration (type, header, value_env)"
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional metadata (e.g., {'inline': true} for LangChain tools)"
    )
    
    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        return v.strip()
    
    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v: str) -> str:
        return v.strip()
    
    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        v_lower = v.lower()
        valid_protocols = ["rest", "grpc", "mcp", "websocket"]
        if v_lower not in valid_protocols:
            raise ValueError(f"protocol must be one of: {', '.join(valid_protocols)}")
        return v_lower
    
    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        v_lower = v.lower()
        valid_statuses = ["active", "inactive"]
        if v_lower not in valid_statuses:
            raise ValueError(f"status must be one of: {', '.join(valid_statuses)}")
        return v_lower
    
    @field_validator("endpoint")
    @classmethod
    def sanitize_endpoint(cls, v: str | None) -> str | None:
        if v is None:
            return None
        sanitized = v.strip()
        return sanitized or None

# ========== Response Schemas ==========

class ToolResponse(BaseModel):
    """Response schema for single tool."""
    
    id: str
    name: str
    description: str
    protocol: str
    endpoint: str | None
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    auth_config: dict[str, Any]
    version: str
    status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}
    
    @classmethod
    def from_dto(cls, dto: ToolResponseDTO) -> "ToolResponse":
        """Create ToolResponse from ToolResponseDTO."""
        return cls(
            id=dto.id,
            name=dto.name,
            description=dto.description,
            protocol=dto.protocol,
            endpoint=dto.endpoint,
            input_data=dto.parameters_schema,
            output_data=dto.response_schema,
            auth_config=dto.auth_config,
            version=dto.version,
            status=dto.status,
            metadata=dto.metadata,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
        )


class ToolSummaryResponse(BaseModel):
    """Response schema for tool summary info (used in list endpoints)."""
    
    id: str
    name: str
    description: str
    version: str
    status: str
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}
    
    @classmethod
    def from_dto(cls, dto: ToolResponseDTO) -> "ToolSummaryResponse":
        """Create ToolSummaryResponse from ToolResponseDTO."""
        return cls(
            id=dto.id,
            name=dto.name,
            description=dto.description,
            version=dto.version,
            status=dto.status,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
        )


class ToolListResponse(BaseModel):
    """Response schema for list of tools."""
    
    tools: list[ToolResponse]
    total: int


class ToolSummaryListResponse(BaseModel):
    """Response schema for list of tools with summary info."""
    
    tools: list[ToolSummaryResponse]
    total: int
    
class DeleteToolResponse(BaseModel):
    """Response schema for DELETE /tools/{tool_id}."""
    
    message: str
    deleted_tool_id: str


class ErrorResponse(BaseModel):
    """Standard error response schema."""
    
    error: str
    detail: str | None = None
    status_code: int
