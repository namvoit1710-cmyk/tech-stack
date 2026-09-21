"""Agent management API endpoints (Layer 3: Presentation)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from dependency_injector.wiring import inject, Provide

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import InvalidDataException
from app.layer3_presentation.dependencies.agent_visibility import get_visible_agent_ids
from app.layer3_presentation.dependencies.principal import require_permission
from app.layer2_application.dtos.register_agent_dto import RegisterAgentDTO
from app.layer2_application.dtos.update_agent_dto import UpdateAgentDTO
from app.layer2_application.use_cases.register_agent import RegisterAgentUseCase
from app.layer2_application.use_cases.update_agent import UpdateAgentUseCase
from app.layer2_application.use_cases.remove_agent import RemoveAgentUseCase
from app.layer2_application.use_cases.activate_agent import ActivateAgentUseCase
from app.layer2_application.use_cases.deactivate_agent import DeactivateAgentUseCase
from app.layer2_application.use_cases.publish_agent import PublishAgentUseCase
from app.layer2_application.use_cases.unpublish_agent import UnpublishAgentUseCase
from app.layer2_application.use_cases.get_agent_by_criteria import GetAgentByCriteriaUseCase
from app.layer2_application.use_cases.get_available_agents import GetAvailableAgentsUseCase
from app.layer2_application.use_cases.get_all_agents import GetAllAgentsUseCase
from app.layer2_application.use_cases.get_agent_by_id import GetAgentByIdUseCase
from app.layer2_application.use_cases.get_agent_by_ids import GetAgentsByIdsUseCase
from app.layer3_presentation.schemas.agent_schema import (
    AgentSummaryListResponse,
    AgentSummaryResponse,
    RegisterAgentRequest,
    UpdateAgentRequest,
    AgentResponse,
    AgentListResponse,
    DeleteAgentResponse,
    ErrorResponse,
)

# Import Container for dependency injection (will be wired in main.py)
from container import Container


router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# ========== Endpoints ==========

@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentResponse,
    responses={
        201: {"description": "Agent created successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
    },
)
@inject
async def register_agent(
    http_request: Request,
    request: RegisterAgentRequest,
    _: Principal = Depends(require_permission("agent.create")),
    use_case: RegisterAgentUseCase = Depends(Provide[Container.register_agent_use_case]),
) -> AgentResponse:
    """
    UC1: Register a new agent.
    
    - **name**: Agent name (1-255 characters)
    - **description**: Agent description
    - **kind**: Agent type - 'business' or 'technical'
    - **status**: Agent status - 'active' or 'inactive'
    - **config_type**: Configuration type - 'default' or 'custom'
    - **business**: Business context / system prompt for the agent
    - **tools**: Optional list of tool IDs to attach
    - **workflows**: Optional list of workflow IDs to attach
    - **agents**: Optional list of agent IDs to attach
    - **is_published**: Publication status (default: False)
    - **version**: Agent version (default: "1.0.0")
    - **healthcheck_endpoint**: Optional health check URL
    - **invoke_endpoint**: Optional invocation URL
    - **model**: Optional LLM model (e.g. "gpt-4")
    - **provider**: Optional LLM provider (e.g. "openai")
    - **temperature**: Optional LLM temperature setting (0.0 to 2.0)
    - **max_tokens**: Optional maximum number of tokens to generate
    - **timeout_ms**: Optional execution timeout in milliseconds
    - **max_concurrency**: Optional maximum concurrent executions
    - **retry_count**: Optional number of retry attempts
    - **streaming_supported**: Whether streaming responses are supported
    - **metadata**: Optional additional agent metadata
    - **custom_system_prompt**: Optional custom system prompt overriding the default
    - **custom_instructions**: Optional list of custom instructions for the agent
    - **custom_restrictions**: Optional list of custom restrictions for the agent
    - **blocked_topics**: Optional list of topics the agent should avoid
    - **blocked_keywords**: Optional list of keywords the agent should avoid
     """
    token = getattr(http_request.state, "jwt_token", None)
    
    # Convert Pydantic → DTO
    request_dict = request.model_dump()
    dto = RegisterAgentDTO.from_api_request(request_dict)
    # Execute use case
    agent_dto = await use_case.execute(dto, token=token)
    # Convert DTO → Pydantic Response
    return AgentResponse.from_dto(agent_dto)


@router.put(
    "/{agent_id}",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent updated successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def update_agent(
    agent_id: str,
    request: UpdateAgentRequest,
    _: Principal = Depends(require_permission("agent.update")),
    use_case: UpdateAgentUseCase = Depends(Provide[Container.update_agent_use_case]),
) -> AgentResponse:
    """
    UC2: Update an existing agent's configuration.
    
    Only provided fields will be updated. Set field to null to clear optional fields.
    """
    # Convert Pydantic → DTO
    # Get fields that were explicitly set in the request
    request_dict = request.model_dump(exclude_unset=True)
    # Use from_api_request to properly handle sentinel values
    dto = UpdateAgentDTO.from_api_request(request_dict)
    # Execute use case (pass agent_id separately)
    agent_dto = await use_case.execute(agent_id, dto)
    # Convert DTO → Pydantic Response
    return AgentResponse.from_dto(agent_dto)


@router.post(
    "/{agent_id}/activate",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent activated successfully"},
        400: {"model": ErrorResponse, "description": "Cannot activate unpublished agent"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def activate_agent(
    agent_id: str,
    _: Principal = Depends(require_permission("agent.activate")),
    use_case: ActivateAgentUseCase = Depends(Provide[Container.activate_agent_use_case]),
) -> AgentResponse:
    """
    Activate an agent (set status to ACTIVE).
    
    Business rule: Agent must be published (is_published=true) to be activated.
    """
    agent_dto = use_case.execute(agent_id)
    return AgentResponse.from_dto(agent_dto)


@router.post(
    "/{agent_id}/deactivate",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent deactivated successfully"},
        400: {"model": ErrorResponse, "description": "Cannot deactivate unpublished agent"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def deactivate_agent(
    agent_id: str,
    _: Principal = Depends(require_permission("agent.deactivate")),
    use_case: DeactivateAgentUseCase = Depends(Provide[Container.deactivate_agent_use_case]),
) -> AgentResponse:
    """
    Deactivate an agent (set status to INACTIVE).
    
    Business rule: Agent must be published (is_published=true) to be deactivated.
    """
    agent_dto = use_case.execute(agent_id)
    return AgentResponse.from_dto(agent_dto)


@router.post(
    "/{agent_id}/publish",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent published successfully"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def publish_agent(
    agent_id: str,
    _: Principal = Depends(require_permission("agent.publish")),
    use_case: PublishAgentUseCase = Depends(Provide[Container.publish_agent_use_case]),
) -> AgentResponse:
    """
    Publish an agent (set is_published to true).
    
    Published agents can be activated/deactivated.
    """
    agent_dto = use_case.execute(agent_id)
    return AgentResponse.from_dto(agent_dto)


@router.post(
    "/{agent_id}/unpublish",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent unpublished successfully"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def unpublish_agent(
    agent_id: str,
    _: Principal = Depends(require_permission("agent.unpublish")),
    use_case: UnpublishAgentUseCase = Depends(Provide[Container.unpublish_agent_use_case]),
) -> AgentResponse:
    """
    Unpublish an agent (set is_published to false and status to INACTIVE).
    
    Business rule: Unpublished agents are automatically set to INACTIVE status.
    """
    agent_dto = use_case.execute(agent_id)
    return AgentResponse.from_dto(agent_dto)


@router.get(
    "/filter",
    response_model=AgentListResponse,
    responses={
        200: {"description": "Agents retrieved successfully"},
    },
)
@inject
async def get_agents(
    kind: Annotated[str | None, Query(description="Filter by kind: BUSINESS or TECHNICAL")] = None,
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    is_published: Annotated[bool | None, Query(description="Filter by publication status")] = None,
    visible_ids: set[str] | None = Depends(get_visible_agent_ids),
    use_case: GetAgentByCriteriaUseCase = Depends(Provide[Container.get_agent_by_criteria_use_case]),
) -> AgentListResponse:
    """
    UC4: Get agents by criteria (with optional filters). Guarded by `agent.read`;
    results are scoped to the caller's pool-visible agents unless they bypass visibility.
    """
    # Normalize enum values to lowercase for domain layer
    normalized_kind = kind.lower() if kind else None
    normalized_status = status.lower() if status else None
    # Execute use case
    agents_dto = use_case.execute(
        kind=normalized_kind,
        status=normalized_status,
        is_published=is_published,
        visible_ids=visible_ids,
    )
    # Convert DTOs → Pydantic Responses
    agents = [AgentResponse.from_dto(dto) for dto in agents_dto]
    return AgentListResponse(
        agents=agents,
        total=len(agents),
    )


@router.get(
    "/available",
    response_model=AgentListResponse,
    responses={
        200: {"description": "Available agents retrieved successfully"},
    },
)
@inject
def get_available_agents(
    visible_ids: set[str] | None = Depends(get_visible_agent_ids),
    use_case: GetAvailableAgentsUseCase = Depends(Provide[Container.get_available_agents_use_case]),
) -> AgentListResponse:
    """
    UC7: Get all available agents (ACTIVE + alive + published).

    Returns only agents that are:
    - Status = ACTIVE
    - is_alive = True (for technical agents)
    - is_published = True

    Scoping is by RBAC pool visibility (`agent.read` + the caller's pools); super_admin /
    system / `agent.view_all` see everything.
    """
    agents_dto = use_case.execute(visible_ids=visible_ids)
    # Convert DTOs → Pydantic Responses
    agents = [AgentResponse.from_dto(dto) for dto in agents_dto]
    return AgentListResponse(
        agents=agents,
        total=len(agents),
    )


@router.get(
    "/all",
    response_model=AgentSummaryListResponse,
    responses={
        200: {"description": "All agents retrieved successfully"},
    },
)
@inject
def get_all_agents(
    visible_ids: set[str] | None = Depends(get_visible_agent_ids),
    use_case: GetAllAgentsUseCase = Depends(Provide[Container.get_all_agents_use_case]),
) -> AgentSummaryListResponse:
    """
    UC8: Get all agents (no health checks).

    Scoping is by RBAC pool visibility (`agent.read` + the caller's pools); super_admin /
    system / `agent.view_all` see everything.
    """
    agents_dto = use_case.execute(visible_ids=visible_ids)
    # Convert DTOs → Pydantic Responses
    agents = [AgentSummaryResponse.from_dto(dto) for dto in agents_dto]
    return AgentSummaryListResponse(
        agents=agents,
        total=len(agents),
    )


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    responses={
        200: {"description": "Agent retrieved successfully"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def get_agent_by_id(
    agent_id: str,
    visible_ids: set[str] | None = Depends(get_visible_agent_ids),
    use_case: GetAgentByIdUseCase = Depends(Provide[Container.get_agent_by_id_use_case]),
) -> AgentResponse:
    """
    UC9: Get a specific agent by its ID. Guarded by `agent.read`; 404 (not 403) if the
    agent is not visible to the caller (no existence leak).
    """
    agent_dto = use_case.execute(agent_id, visible_ids=visible_ids)
    return AgentResponse.from_dto(agent_dto)
    

@router.post(
    "/by_ids",
    response_model=AgentListResponse,
    responses={
        200: {"description": "Agents retrieved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        404: {"model": ErrorResponse, "description": "One or more agents not found"},
    },
)
@inject
async def get_agents_by_ids(
    ids: list[str],
    visible_ids: set[str] | None = Depends(get_visible_agent_ids),
    use_case: GetAgentsByIdsUseCase = Depends(Provide[Container.get_agents_by_ids_use_case]),
) -> AgentListResponse:
    """
    UC10: Get agents by a list of IDs.
    
    Returns detailed agent information for the specified IDs.
    """
    # Validate that IDs are provided
    if not ids:
        raise InvalidDataException("At least one agent ID must be provided")
    agents_dto = use_case.execute(ids, visible_ids=visible_ids)
    agents = [AgentResponse.from_dto(dto) for dto in agents_dto]
    return AgentListResponse(
        agents=agents,
        total=len(agents),
    )


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_200_OK,
    response_model=DeleteAgentResponse,
    responses={
        200: {"description": "Agent deleted successfully"},
        404: {"model": ErrorResponse, "description": "Agent not found"},
    },
)
@inject
async def delete_agent(
    agent_id: str,
    _: Principal = Depends(require_permission("agent.delete")),
    use_case: RemoveAgentUseCase = Depends(Provide[Container.remove_agent_use_case]),
) -> DeleteAgentResponse:
    """
    UC3: Remove an agent from the registry.
    """
    use_case.execute(agent_id)
    return DeleteAgentResponse(
        message="Agent deleted successfully",
        deleted_agent_id=agent_id,
    )
