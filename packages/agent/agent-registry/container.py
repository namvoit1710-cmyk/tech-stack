"""Dependency Injection Container (Service Locator Pattern)."""
from dependency_injector import containers, providers

from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

from app.layer4_infrastructure.settings import Settings
from app.layer2_application.use_cases.capability_summary_generator import CapabilitySummaryGenerator
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.repositories.hana_agent_repository import HANAAgentRepository
from app.layer4_infrastructure.persistence.repositories.hana_tool_repository import HANAToolRepository
from app.layer4_infrastructure.persistence.repositories.hana_workflow_repository import HANAWorkflowRepository
from app.layer4_infrastructure.external_tools.httpx_health_check_client import HTTPXHealthCheckService
from app.layer4_infrastructure.external_tools.httpx_fetch_workflow_api_client import HttpxFetchWorkflowApiClient
from app.layer4_infrastructure.external_tools.uuidv7_generator import UUIDv7Generator
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
from app.layer2_application.use_cases.register_tool import RegisterToolUseCase
from app.layer2_application.use_cases.activate_tool import ActivateToolUseCase
from app.layer2_application.use_cases.deactivate_tool import DeactivateToolUseCase
from app.layer2_application.use_cases.get_all_tools import GetAllToolsUseCase
from app.layer2_application.use_cases.get_all_workflows import GetAllWorkflowsUseCase
from app.layer2_application.use_cases.get_tool_by_ids import GetToolsByIdsUseCase
from app.layer2_application.use_cases.get_workflow_by_ids import GetWorkflowsByIdsUseCase
from app.layer2_application.use_cases.get_agent_by_ids import GetAgentsByIdsUseCase
from app.layer2_application.use_cases.remove_tool import RemoveToolUseCase
from app.layer2_application.use_cases.health_check_agents import HealthCheckAgentsUseCase
from app.layer2_application.use_cases.get_tool_by_id import GetToolByIdUseCase
from app.layer2_application.use_cases.get_workflow_by_id import GetWorkflowByIdUseCase
from app.layer4_infrastructure.external_tools.httpx_fetch_current_user_api_client import HttpxFetchCurrentUserInfoApiClient
from app.layer4_infrastructure.annotation.anyio_async_executor import AsyncExecutor
from app.layer4_infrastructure.persistence.repositories.hana_rbac_user_repository import HANARbacUserRepository
from app.layer4_infrastructure.persistence.repositories.hana_role_repository import HANARoleRepository
from app.layer4_infrastructure.persistence.repositories.hana_permission_repository import HANAPermissionRepository
from app.layer4_infrastructure.security.jwt_identity_token_reader import JwtIdentityTokenReader
from app.layer4_infrastructure.logger.app_logger import get_logger
from app.layer2_application.use_cases.resolve_principal import ResolvePrincipalUseCase
from app.layer2_application.use_cases.list_permissions import ListPermissionsUseCase
from app.layer2_application.use_cases.list_roles import ListRolesUseCase
from app.layer2_application.use_cases.create_role import CreateRoleUseCase
from app.layer2_application.use_cases.update_role import UpdateRoleUseCase
from app.layer2_application.use_cases.set_role_permissions import SetRolePermissionsUseCase
from app.layer2_application.use_cases.delete_role import DeleteRoleUseCase
from app.layer2_application.use_cases.assign_role import AssignRoleUseCase
from app.layer2_application.use_cases.list_users import ListUsersUseCase
from app.layer4_infrastructure.persistence.repositories.hana_agent_pool_repository import HANAAgentPoolRepository
from app.layer2_application.use_cases.create_agent_pool import CreateAgentPoolUseCase
from app.layer2_application.use_cases.get_agent_pool import GetAgentPoolUseCase
from app.layer2_application.use_cases.list_agent_pools import ListAgentPoolsUseCase
from app.layer2_application.use_cases.update_agent_pool import UpdateAgentPoolUseCase
from app.layer2_application.use_cases.delete_agent_pool import DeleteAgentPoolUseCase
from app.layer2_application.use_cases.add_agent_to_pool import AddAgentToPoolUseCase
from app.layer2_application.use_cases.remove_agent_from_pool import RemoveAgentFromPoolUseCase
from app.layer2_application.use_cases.add_member_to_pool import AddMemberToPoolUseCase
from app.layer2_application.use_cases.remove_member_from_pool import RemoveMemberFromPoolUseCase

class Container(containers.DeclarativeContainer):
    """Application dependency injection container."""
    
    # ========== Layer 4: Infrastructure ==========
    
    settings = providers.Singleton(Settings)
    
    database_factory = providers.Singleton(
        DatabaseFactory,
        settings=settings.provided.get_database_settings.call(),
    )
    
    # External Tools
    health_check_client = providers.Singleton(
        HTTPXHealthCheckService,
        settings=settings,
    )
    
    # Repositories
    agent_repository = providers.Singleton(
        HANAAgentRepository,
        db_factory=database_factory,
    )
    
    tool_repository = providers.Singleton(
        HANAToolRepository,
        db_factory=database_factory,
    )
    
    workflow_repository = providers.Singleton(
        HANAWorkflowRepository,
        db_factory=database_factory,
    )


    fetch_workflow_api_client = providers.Singleton(
        HttpxFetchWorkflowApiClient,
        settings=settings,
    )
    
    fetch_current_user_info_api_client = providers.Singleton(
        HttpxFetchCurrentUserInfoApiClient,
        settings=settings,
    )

    uuid_generator = providers.Singleton(UUIDv7Generator)

    async_executor = providers.ThreadSafeSingleton(
        AsyncExecutor,
    )

    llm_service = providers.Singleton(
        make_llm_service,
        agent_type="agent-registry",
    )

    capability_summary_generator = providers.Singleton(
        CapabilitySummaryGenerator,
        llm_service=llm_service,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.capability_summary_generator"),
    )

    # ---- RBAC (SA RBAC v1) — user mirror repository + JWT identity reader ----
    # (resolve_principal_use_case is defined at the end of the container, near the
    #  other use cases; keep it single — see the "RBAC: Resolve Principal (T2)" block.)
    rbac_user_repository = providers.Singleton(
        HANARbacUserRepository,
        db_factory=database_factory,
    )

    identity_token_reader = providers.Singleton(JwtIdentityTokenReader)

    # Role / permission management (Task 3)
    role_repository = providers.Singleton(
        HANARoleRepository,
        db_factory=database_factory,
    )
    permission_repository = providers.Singleton(
        HANAPermissionRepository,
        db_factory=database_factory,
    )

    list_permissions_use_case = providers.Factory(
        ListPermissionsUseCase, repository=permission_repository
    )
    list_roles_use_case = providers.Factory(ListRolesUseCase, repository=role_repository)
    create_role_use_case = providers.Factory(CreateRoleUseCase, repository=role_repository)
    update_role_use_case = providers.Factory(UpdateRoleUseCase, repository=role_repository)
    set_role_permissions_use_case = providers.Factory(
        SetRolePermissionsUseCase,
        role_repository=role_repository,
        permission_repository=permission_repository,
    )
    delete_role_use_case = providers.Factory(DeleteRoleUseCase, repository=role_repository)

    list_users_use_case = providers.Factory(ListUsersUseCase, repository=rbac_user_repository)
    assign_role_use_case = providers.Factory(
        AssignRoleUseCase,
        user_repository=rbac_user_repository,
        role_repository=role_repository,
        fetch_current_user_info_api_client=fetch_current_user_info_api_client,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.assign_role"),
    )

    # Agent pools (Task 4)
    agent_pool_repository = providers.Singleton(
        HANAAgentPoolRepository,
        db_factory=database_factory,
    )
    create_agent_pool_use_case = providers.Factory(CreateAgentPoolUseCase, repository=agent_pool_repository)
    get_agent_pool_use_case = providers.Factory(GetAgentPoolUseCase, repository=agent_pool_repository)
    list_agent_pools_use_case = providers.Factory(ListAgentPoolsUseCase, repository=agent_pool_repository)
    update_agent_pool_use_case = providers.Factory(UpdateAgentPoolUseCase, repository=agent_pool_repository)
    delete_agent_pool_use_case = providers.Factory(DeleteAgentPoolUseCase, repository=agent_pool_repository)
    add_agent_to_pool_use_case = providers.Factory(
        AddAgentToPoolUseCase, pool_repository=agent_pool_repository, agent_repository=agent_repository
    )
    remove_agent_from_pool_use_case = providers.Factory(
        RemoveAgentFromPoolUseCase, repository=agent_pool_repository
    )
    add_member_to_pool_use_case = providers.Factory(
        AddMemberToPoolUseCase,
        pool_repository=agent_pool_repository,
        user_repository=rbac_user_repository,
        fetch_current_user_info_api_client=fetch_current_user_info_api_client,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.add_member_to_pool"),
    )
    remove_member_from_pool_use_case = providers.Factory(
        RemoveMemberFromPoolUseCase, repository=agent_pool_repository
    )

    # ========== Layer 2: Application (Use Cases) ==========
    
    # UC1: Register Agent
    register_agent_use_case = providers.Factory(
        RegisterAgentUseCase,
        settings=settings,
        repository=agent_repository,
        tool_repository=tool_repository,
        workflow_repository=workflow_repository,
        fetch_workflow_api_client=fetch_workflow_api_client,
        uuid_generator=uuid_generator,
        async_executor=async_executor,
        capability_summary_generator=capability_summary_generator,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.register_agent"),
    )

    # UC2: Update Agent
    update_agent_use_case = providers.Factory(
        UpdateAgentUseCase,
        repository=agent_repository,
        tool_repository=tool_repository,
        workflow_repository=workflow_repository,
        fetch_workflow_api_client=fetch_workflow_api_client,
        async_executor=async_executor,
        capability_summary_generator=capability_summary_generator,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.update_agent"),
    )
    
    # UC3: Remove Agent
    remove_agent_use_case = providers.Factory(
        RemoveAgentUseCase,
        repository=agent_repository,
    )
    
    # Activate Agent
    activate_agent_use_case = providers.Factory(
        ActivateAgentUseCase,
        repository=agent_repository,
    )
    
    # Deactivate Agent
    deactivate_agent_use_case = providers.Factory(
        DeactivateAgentUseCase,
        repository=agent_repository,
    )
    
    # Publish Agent
    publish_agent_use_case = providers.Factory(
        PublishAgentUseCase,
        repository=agent_repository,
    )
    
    # Unpublish Agent
    unpublish_agent_use_case = providers.Factory(
        UnpublishAgentUseCase,
        repository=agent_repository,
    )
    
    # UC4: Get Agent by Criteria
    get_agent_by_criteria_use_case = providers.Factory(
        GetAgentByCriteriaUseCase,
        repository=agent_repository,
    )
    
    # UC7: Get Available Agents
    get_available_agents_use_case = providers.Factory(
        GetAvailableAgentsUseCase,
        repository=agent_repository
    )
    
    # UC8: Get All Agents
    get_all_agents_use_case = providers.Factory(
        GetAllAgentsUseCase,
        repository=agent_repository,
    )

    # UC9: Get Agent by ID
    get_agent_by_id_use_case = providers.Factory(
        GetAgentByIdUseCase,
        repository=agent_repository,
    )
    
    # UC-RegisterTool: Register Tool
    register_tool_use_case = providers.Factory(
        RegisterToolUseCase,
        repository=tool_repository,
        uuid_generator=uuid_generator,
    )
    
    # UC-GetToolById: Get Tool by ID
    get_tool_by_id_use_case = providers.Factory(
        GetToolByIdUseCase,
        repository=tool_repository,
    )

    # UC-ActivateTool: Activate Tool
    activate_tool_use_case = providers.Factory(
        ActivateToolUseCase,
        repository=tool_repository,
    )

    # UC-DeactivateTool: Deactivate Tool
    deactivate_tool_use_case = providers.Factory(
        DeactivateToolUseCase,
        repository=tool_repository,
    )
    
    # UC-GetAllTools: Get All Tools
    get_all_tools_use_case = providers.Factory(
        GetAllToolsUseCase,
        repository=tool_repository,
    )
    
    # UC-RemoveTool: Remove Tool
    remove_tool_use_case = providers.Factory(
        RemoveToolUseCase,
        repository=tool_repository,
    )
    
    # UC-GetAllWorkflows: Get All Workflows
    get_all_workflows_use_case = providers.Factory(
        GetAllWorkflowsUseCase,
        repository=workflow_repository,
    )

    # UC-GetWorkflowsByIds: Get Workflows by IDs
    get_workflows_by_ids_use_case = providers.Factory(
        GetWorkflowsByIdsUseCase,
        repository=workflow_repository,
    )
    
    # UC-GetWorkflowById: Get Workflow by ID
    get_workflow_by_id_use_case = providers.Factory(
        GetWorkflowByIdUseCase,
        repository=workflow_repository,
    )
    
    # UC-GetToolsByIds: Get Tools by IDs
    get_tools_by_ids_use_case = providers.Factory(
        GetToolsByIdsUseCase,
        repository=tool_repository,
    )
    
    # UC-GetAgentsByIds: Get Agents by IDs
    get_agents_by_ids_use_case = providers.Factory(
        GetAgentsByIdsUseCase,
        repository=agent_repository,
    )
    
    # UC-HealthCheckAgents: Health Check Agents
    health_check_agents_use_case = providers.Factory(
        HealthCheckAgentsUseCase,
        agent_repository=agent_repository,
        health_check_client=health_check_client,
        async_executor=async_executor,
        logger=providers.Factory(get_logger, "app.layer2_application.use_cases.health_check_agents"),
    )

    # RBAC: Resolve Principal (T2) — Factory, consistent with the other use-case
    # providers (stateless UC; deps are singletons). Logger injected via ILogger port.
    resolve_principal_use_case = providers.Factory(
        ResolvePrincipalUseCase,
        user_repository=rbac_user_repository,
        identity_token_reader=identity_token_reader,
        fetch_current_user_info_api_client=fetch_current_user_info_api_client,
        trust_unauthenticated_internal=settings.provided.trust_unauthenticated_internal,
        logger=providers.Factory(
            get_logger,
            "app.layer2_application.use_cases.resolve_principal",
        ),
    )