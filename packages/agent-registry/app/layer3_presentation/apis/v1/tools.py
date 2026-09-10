"""Tool management API endpoints (Layer 3: Presentation)."""

from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from app.layer1_domain.exceptions import InvalidDataException
from app.layer2_application.dtos.register_tool_dto import RegisterToolDTO
from app.layer2_application.use_cases.activate_tool import ActivateToolUseCase
from app.layer2_application.use_cases.deactivate_tool import DeactivateToolUseCase
from app.layer2_application.use_cases.register_tool import RegisterToolUseCase
from app.layer2_application.use_cases.get_all_tools import GetAllToolsUseCase
from app.layer2_application.use_cases.get_tool_by_ids import GetToolsByIdsUseCase
from app.layer3_presentation.schemas.tool_schema import (
    DeleteToolResponse,
    RegisterToolRequest,
    ToolListResponse,
    ToolResponse,
    ToolSummaryResponse,
    ToolSummaryListResponse,
    ErrorResponse,
)
 # DomainExceptionHandler removed; use global error handler

# Import Container for dependency injection (will be wired in bootstrap.py)
from container import Container
from app.layer2_application.use_cases.remove_tool import RemoveToolUseCase
from app.layer2_application.use_cases.get_tool_by_id import GetToolByIdUseCase


router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

# ========== API Endpoints ==========

@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=ToolResponse,
    responses={
        201: {"description": "Tool registered successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
    },
)
@inject
async def register_tool(
    request: RegisterToolRequest,
    use_case: RegisterToolUseCase = Depends(Provide[Container.register_tool_use_case]),
) -> ToolResponse:
    """
    UC-RegisterTool: Register a new tool in the registry.
    
    - **name**: Tool name (e.g. "WeatherAPI", "PythonExecutor", etc.)
    - **description**: Brief description of the tool's functionality
    - **protocol**: Protocol of the tool (e.g. "REST", "gRPC", "MCP", "LangChain", etc.)
    - **endpoint**: Endpoint URL of the tool (e.g. "https://api.weather.com/v1", "inline", etc.)
    - **version**: Version of the tool (default: "1.0.0")
    - **status**: Status of the tool (default: "active", other values: "inactive")
    
    """
    
    # Convert request → DTO
    request_dict = request.model_dump()
    dto = RegisterToolDTO.from_api_request(request_dict)
    # Execute use case
    tool_dto = use_case.execute(dto)
    # Return response with new tool ID
    return ToolResponse.from_dto(tool_dto)


@router.get(
    "/all",
    response_model=ToolSummaryListResponse,
    responses={
        200: {"description": "All tools retrieved successfully"},
    },
)
@inject
async def get_all_tools(
    use_case: GetAllToolsUseCase = Depends(Provide[Container.get_all_tools_use_case]),
) -> ToolSummaryListResponse:
    """
    UC-GetAllTools: Get all tools from the registry.
    
    Returns a complete list of all registered tools including both
    protocol-based tools (REST, gRPC, MCP, etc.) and inline LangChain tools.
    """
    tools_dto = use_case.execute()
    # Convert DTOs → Pydantic Responses (summary)
    tools = [ToolSummaryResponse.from_dto(dto) for dto in tools_dto]
    return ToolSummaryListResponse(
        tools=tools,
        total=len(tools),
    )
    
@router.post(
    "/by_ids",
    response_model=ToolListResponse,
    responses={
        200: {"description": "Tools retrieved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        404: {"model": ErrorResponse, "description": "One or more tools not found"},
    },
)
@inject
async def get_tools_by_ids(
    ids: list[str],
    use_case: GetToolsByIdsUseCase = Depends(Provide[Container.get_tools_by_ids_use_case]),
) -> ToolListResponse:
    """
    UC-GetToolsByIds: Get tools by their IDs.
    
    Accepts a list of tool IDs and returns the corresponding tools.
    """
    if not ids:
        raise InvalidDataException("At least one tool ID must be provided")
    tools_dto = use_case.execute(ids)
    # Convert DTOs → Pydantic Responses (summary)
    tools = [ToolResponse.from_dto(dto) for dto in tools_dto]
    return ToolListResponse(
        tools=tools,
        total=len(tools),
    )
    
@router.get(
    "/{tool_id}",
    response_model=ToolResponse,
    responses={
        200: {"description": "Tool retrieved successfully"},
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
@inject
async def get_tool_by_id(
    tool_id: str,
    use_case: GetToolByIdUseCase = Depends(Provide[Container.get_tool_by_id_use_case]),
) -> ToolResponse:
    """
    Get a single tool by its ID.
    
    This endpoint is useful for retrieving detailed information about a specific tool.
    """
    tool_dto = use_case.execute(tool_id)
    return ToolResponse.from_dto(tool_dto)


@router.post(
    "/{tool_id}/activate",
    response_model=ToolResponse,
    responses={
        200: {"description": "Tool activated successfully"},
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
@inject
async def activate_tool(
    tool_id: str,
    use_case: ActivateToolUseCase = Depends(Provide[Container.activate_tool_use_case]),
) -> ToolResponse:
    """Activate a tool (set status to ACTIVE)."""
    tool_dto = use_case.execute(tool_id)
    return ToolResponse.from_dto(tool_dto)


@router.post(
    "/{tool_id}/deactivate",
    response_model=ToolResponse,
    responses={
        200: {"description": "Tool deactivated successfully"},
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
@inject
async def deactivate_tool(
    tool_id: str,
    use_case: DeactivateToolUseCase = Depends(Provide[Container.deactivate_tool_use_case]),
) -> ToolResponse:
    """Deactivate a tool (set status to INACTIVE)."""
    tool_dto = use_case.execute(tool_id)
    return ToolResponse.from_dto(tool_dto)
    
@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_200_OK,
    response_model=DeleteToolResponse,
    responses={
        200: {"description": "Tool deleted successfully"},
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
@inject
async def delete_tool(
    tool_id: str,
    use_case: RemoveToolUseCase = Depends(Provide[Container.remove_tool_use_case]),
) -> DeleteToolResponse:
    """
    UC-RemoveTool: Remove a tool from the registry.
    """
    use_case.execute(tool_id)
    return DeleteToolResponse(
        message="Tool deleted successfully",
        deleted_tool_id=tool_id,
    )
