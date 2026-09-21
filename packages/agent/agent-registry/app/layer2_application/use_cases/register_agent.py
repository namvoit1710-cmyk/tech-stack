"""UC1: Register Agent use case."""

from app.layer1_domain.entities.agent import Agent, AgentKind, AgentStatus, ConfigType
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import (
    InvalidDataException,
    InvalidOperationException,
    AlreadyExistsException,
)
from app.layer2_application.dtos.register_agent_dto import RegisterAgentDTO
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository
from app.layer2_application.interfaces.tool_repository_port import IToolRepository
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository
from app.layer2_application.interfaces.fetch_workflow_api_client import IFetchWorkflowApiClient
from app.layer2_application.interfaces.register_agent_setting_port import IRegisterAgentSettingPort
from app.layer2_application.interfaces.uuid_generator_port import IUUIDGeneratorPort
from app.layer2_application.use_cases.capability_summary_generator import CapabilitySummaryGenerator
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger
from app.layer2_application.interfaces.async_executor_port import AsyncExecutorInterface
from app.layer1_domain.entities.tool import Tool

class RegisterAgentUseCase:
    """Use case for registering a new agent in the registry.
    
    Business rules:
    - Capabilities are built from attached tool and workflow descriptions
    """

    def __init__(
        self,
        repository: IAgentRepository,
        tool_repository: IToolRepository,
        workflow_repository: IWorkflowRepository,
        fetch_workflow_api_client: IFetchWorkflowApiClient,
        settings: IRegisterAgentSettingPort,
        uuid_generator: IUUIDGeneratorPort,
        async_executor: AsyncExecutorInterface,
        capability_summary_generator: CapabilitySummaryGenerator,
        logger: ILogger | None = None,
    ):
        """Initialize use case with repository dependencies.

        Args:
            repository: Agent repository port implementation
            tool_repository: Tool repository port implementation
            workflow_repository: Workflow repository port implementation
            fetch_workflow_api_client: Client for fetching workflow data from external API
            settings: Application settings
            uuid_generator: UUID generator port implementation
            capability_summary_generator: LLM-backed capability summary generator
        """
        self.agent_repository = repository
        self.tool_repository = tool_repository
        self.workflow_repository = workflow_repository
        self.fetch_workflow_api_client = fetch_workflow_api_client
        self.settings = settings
        self.uuid_generator = uuid_generator
        self.async_executor = async_executor
        self.capability_summary_generator = capability_summary_generator
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(self, dto: RegisterAgentDTO, token: str | None = None) -> AgentResponseDTO:
        """Register a new agent.
        
        Args:
            dto: Register agent data transfer object
            token: JWT token of the user performing the action
            
        Returns:
            AgentResponseDTO: Registered agent details
            
        Raises:
            NotFoundException: If a provided tool, workflow, or agent ID does not exist
        """
        
        # RBAC (T5): the Registry no longer derives creator identity from the token.
        # agents.user_email/tenant_id/user_roles columns are KEPT but no longer populated
        # (removal deferred, B2); access control is now via pools + permissions.
        user_email = None
        user_name = None
        tenant_id = None
        user_roles = []

        # Parse enum values
        try:
            kind = AgentKind(dto.kind)
        except ValueError:
            supported_kinds = [k.value for k in AgentKind]
            raise InvalidDataException(f"Invalid agent kind: {dto.kind}. Must be one of: {', '.join(supported_kinds)}")
        
        try:
            status = AgentStatus(dto.status)
        except ValueError:
            supported_statuses = [s.value for s in AgentStatus]
            raise InvalidDataException(f"Invalid agent status: {dto.status}. Must be one of: {', '.join(supported_statuses)}")
        
        try:
            config_type = ConfigType(dto.config_type)
        except ValueError:
            supported_config_types = [c.value for c in ConfigType]
            raise InvalidDataException(f"Invalid config type: {dto.config_type}. Must be one of: {', '.join(supported_config_types)}")
        
        # SA-1938: provider / model / temperature are chosen by the caller and only
        # stored here. No allowlist and no default - omitted means omitted (NULL).
        
        # SA-1072/#9: fail fast on a duplicate (name, version) BEFORE persisting any workflow,
        # so a duplicate register never leaves orphaned workflow rows behind — the workflow
        # upserts below commit in their own sessions, ahead of the agent save.
        existing_agent = await self.async_executor.run_sync(
            self.agent_repository.find_by_name_and_version, dto.name, dto.version
        )
        if existing_agent:
            raise AlreadyExistsException(entity="Agent", entity_name=f"{dto.name}:{dto.version}")

        # Sync capabilities based on relationships - will raise NotFoundException if any related entity does not exist
        capabilities, capability_summary = await self._sync_capabilities(
            description=dto.description,
            workflows=dto.workflows,
            tools=dto.tools,
            agents=dto.agents
        )
        
        # Create domain entity (validates business rules)
        # Note: provider and model are required but not in DTO - using defaults for now
        agent = Agent.create(
            id=self.uuid_generator.generate_uuid(),
            name=dto.name,
            kind=kind,
            status=status,
            description=dto.description,
            version=dto.version,
            is_published=dto.is_published,
            healthcheck_endpoint=dto.healthcheck_endpoint,
            invoke_endpoint=dto.invoke_endpoint,
            provider=dto.provider,
            model=dto.model,
            temperature=dto.temperature,  # SA-1938: no default - None means "not chosen"
            max_tokens=dto.max_tokens if dto.max_tokens is not None else self.settings.default_max_tokens,
            system_prompt=dto.business,  # Use business field as system prompt
            config_type=config_type,
            timeout_ms=dto.timeout_ms if dto.timeout_ms is not None else self.settings.default_timeout_ms,
            max_concurrency=dto.max_concurrency if dto.max_concurrency is not None else self.settings.default_max_concurrency,
            retry_count=dto.retry_count if dto.retry_count is not None else self.settings.default_retry_count,
            streaming_supported=dto.streaming_supported if dto.streaming_supported is not None else self.settings.default_streaming_supported,
            capabilities=capabilities,
            capability_summary=capability_summary,
            agents=dto.agents or [],
            knowledge_base=dto.knowledge_base or [],
            metadata=dto.metadata,
            tools=dto.tools or [],
            workflows=dto.workflows or [],
            custom_system_prompt=dto.custom_system_prompt,
            custom_instructions=dto.custom_instructions,
            custom_restrictions=dto.custom_restrictions,
            blocked_topics=dto.blocked_topics,
            blocked_keywords=dto.blocked_keywords,
            user_email=user_email,
            tenant_id=tenant_id,
            user_roles=user_roles
        )

        try:
            # Persist agent and create relationships in a single transaction
            await self.async_executor.run_sync(self.agent_repository.save, agent, tool_ids=agent.tools, workflow_ids=agent.workflows)
        except AlreadyExistsException as e:
            self._logger.error(
                "agent_save_failed",
                message=f"Failed to save agent {agent.id} into repository: {str(e)}"
            )
            raise
        except Exception as e:
            self._logger.error(
                "agent_save_failed",
                message=f"Failed to save agent {agent.id} into repository: {str(e)}"
            )
            raise InvalidDataException(f"Failed to save agent {agent.id} into repository: {str(e)}") from e

        # Return DTO
        return AgentResponseDTO.from_entity(agent)

    async def _fetch_and_persist_workflow(self, workflow_id: str) -> Workflow:
        """Fetch workflow from external API and persist to repository."""
        fetched_workflow = await self.fetch_workflow_api_client.fetch_workflow(workflow_id)
        fetched_workflow_entity = fetched_workflow.to_domain_entity()
        
        # Check if workflow already exists in repository with same content to avoid unnecessary upsert
        existing_workflow = await self.async_executor.run_sync(self.workflow_repository.find_by_id, workflow_id)
        if existing_workflow and existing_workflow.equals_content(fetched_workflow_entity):
            self._logger.info(
                "workflow_already_exists",
                message=f"Workflow {workflow_id} already exists in repository with same content - skipping persistence"
            )
            return existing_workflow
        
        # Update created_at to workflow if workflow already exists in repository to preserve original creation time
        # updated_date already set to now() in domain entity constructor
        if existing_workflow:
            fetched_workflow_entity.created_at = existing_workflow.created_at
        

        # Persist workflow
        try:
            upserted_workflow = await self.async_executor.run_sync(self.workflow_repository.upsert, fetched_workflow_entity)
            self._logger.info(
                "workflow_upserted",
                message=f"Workflow {workflow_id} upserted into repository"
            )
            return upserted_workflow
        except Exception as e:
            self._logger.error(
                "workflow_upsert_failed",
                message=f"Failed to upsert workflow {workflow_id} into repository: {str(e)}"
            )
            raise InvalidOperationException(f"Failed to upsert workflow {workflow_id} into repository: {str(e)}") from e
        
    
    def _resolve_workflow_capabilities(self, workflows: list[Workflow]) -> list[str]:
        """SA-963: map workflows -> capability strings.

        - Skip a workflow that has neither a name nor a description.
        - Fall back to the workflow name when the description is empty.
        - Otherwise use the description.
        Pure/synchronous so it stays cheap and directly unit-testable.
        """
        resolved: list[str] = []
        for wf in workflows:
            if wf.description is None or wf.description.strip() == "":
                if wf.name is None or wf.name.strip() == "":
                    self._logger.warning(f"Workflow {wf.id} has no name or description - skipping adding to capabilities")
                    continue
                self._logger.warning(f"Workflow {wf.id} has no description - using name as capability")
                resolved.append(wf.name)
            else:
                resolved.append(wf.description)
        return resolved

    async def _sync_capabilities(
        self, description: str, workflows: list[str], tools: list[str], agents: list[str]
    ) -> tuple[list[str], str]:
        """Sync agent capabilities and capability_summary based on current relationships."""
        capabilities = []
        workflow_descriptions: list[str] = []
        tool_descriptions: list[str] = []
        agent_descriptions: list[str] = []

        # Fetch current workflows and tools to build capabilities
        if workflows:
            fetched_workflows = [
                await self._fetch_and_persist_workflow(workflow_id) for workflow_id in workflows
            ]
            # SA-963: skip a workflow with neither name nor description; fall back to name when
            # description is empty. Pure logic lives in a sync helper (fast + directly testable).
            workflow_descriptions = self._resolve_workflow_capabilities(fetched_workflows)
            capabilities.extend(workflow_descriptions)

        if tools:
            # Repo will raise NotFoundException if any tool ID does not exist
            existing_tools = await self.async_executor.run_sync(self.tool_repository.find_by_ids, tools)
            tool_descriptions = [tool.description for tool in existing_tools]
            capabilities.extend(tool_descriptions)

        if agents:
            # Repo will raise NotFoundException if any agent ID does not exist
            existing_agents = await self.async_executor.run_sync(self.agent_repository.find_by_ids, agents)
            agent_descriptions = [agent.description for agent in existing_agents]
            capabilities.extend(agent_descriptions)

        capability_summary = await self.capability_summary_generator.generate(
            description=description,
            tool_descriptions=tool_descriptions,
            workflow_descriptions=workflow_descriptions,
            agent_descriptions=agent_descriptions,
        )

        return capabilities, capability_summary

