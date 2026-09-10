"""Workflow management API endpoints (Layer 3: Presentation)."""

from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from app.layer1_domain.exceptions import (
    InvalidDataException,
)
from app.layer2_application.use_cases.get_all_workflows import GetAllWorkflowsUseCase
from app.layer2_application.use_cases.get_workflow_by_ids import GetWorkflowsByIdsUseCase
from app.layer3_presentation.schemas.workflow_schema import (
    WorkflowResponse,
    WorkflowListResponse,
    ErrorResponse,
)

# Import Container for dependency injection (will be wired in bootstrap.py)
from container import Container
from app.layer2_application.use_cases.get_workflow_by_id import GetWorkflowByIdUseCase


router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

# ========== API Endpoints ==========

@router.get(
    "/all",
    response_model=WorkflowListResponse,
    responses={
        200: {"description": "All workflows retrieved successfully"},
    },
)
@inject
async def get_all_workflows(
    use_case: GetAllWorkflowsUseCase = Depends(Provide[Container.get_all_workflows_use_case]),
) -> WorkflowListResponse:
    """
    UC-GetAllWorkflows: Get all workflows from the registry.
    
    Returns a complete list of all registered workflows that agents can execute.
    """
    workflows_dto = use_case.execute()
    # Convert DTOs → Pydantic Responses
    workflows = [WorkflowResponse.from_dto(dto) for dto in workflows_dto]
    return WorkflowListResponse(
        workflows=workflows,
        total=len(workflows),
    )
    
    
@router.post(
    "/by_ids",
    response_model=WorkflowListResponse,
    responses={
        200: {"description": "Workflows retrieved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        404: {"model": ErrorResponse, "description": "One or more workflows not found"},
    },
)
@inject
async def get_workflows_by_ids(
    ids: list[str],
    use_case: GetWorkflowsByIdsUseCase = Depends(Provide[Container.get_workflows_by_ids_use_case]),
) -> WorkflowListResponse:
    """
    UC-GetWorkflowsByIds: Get workflows by their IDs.
    
    Accepts a list of workflow IDs and returns the corresponding workflows.
    """
    if not ids:
        raise InvalidDataException("At least one workflow ID must be provided")
    workflows_dto = use_case.execute(ids)
    # Convert DTOs → Pydantic Responses
    workflows = [WorkflowResponse.from_dto(dto) for dto in workflows_dto]
    return WorkflowListResponse(
        workflows=workflows,
        total=len(workflows),
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    responses={
        200: {"description": "Workflow retrieved successfully"},
        404: {"model": ErrorResponse, "description": "Workflow not found"},
    },
)
@inject
async def get_workflow_by_id(
    workflow_id: str,
    use_case: GetWorkflowByIdUseCase = Depends(Provide[Container.get_workflow_by_id_use_case]),
) -> WorkflowResponse:
    """
    UC-GetWorkflowById: Get a single workflow by its ID.
    
    Accepts a workflow ID and returns the corresponding workflow details.
    """
    workflow_dto = use_case.execute(workflow_id)
    return WorkflowResponse.from_dto(workflow_dto)