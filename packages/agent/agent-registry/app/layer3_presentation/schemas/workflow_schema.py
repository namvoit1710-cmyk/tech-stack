"""Pydantic schemas for Workflow API endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO


class WorkflowResponse(BaseModel):
    """Response schema for single workflow."""
    
    id: str
    name: str
    description: str
    version: str
    status: str
    input_schema: list
    output_schema: list
    main_flow: bool
    metadata: dict
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}
    
    @classmethod
    def from_dto(cls, dto: WorkflowResponseDTO) -> "WorkflowResponse":
        """Create WorkflowResponse from WorkflowResponseDTO."""
        return cls(
            id=dto.id,
            name=dto.name,
            description=dto.description,
            version=dto.version,
            status=dto.status,
            input_schema=dto.input_schema,
            output_schema=dto.output_schema,
            main_flow=dto.main_flow,
            metadata=dto.metadata,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
        )


class WorkflowListResponse(BaseModel):
    """Response schema for list of workflows."""
    
    workflows: list[WorkflowResponse]
    total: int

class ErrorResponse(BaseModel):
    """Standard error response schema."""
    
    error: str
    detail: str | None = None
    status_code: int


# ========== Fetching Schemas ==========

class WorkflowFetchDataResponse(BaseModel):
    """Response schema for workflow data fetched from external API."""
    
    id: str
    name: str
    description: str
    version: str
    status: str
    main_flow: bool = False
    input_schema: list | None = None
    output_schema: list | None = None
    metadata: dict | None = None

class WorkflowFetchResponse(BaseModel):
    """Response schema for workflow data fetched from external API."""
    
    status: str
    data: WorkflowFetchDataResponse | None = None