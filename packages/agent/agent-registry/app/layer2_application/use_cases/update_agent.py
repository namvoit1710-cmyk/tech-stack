"""UC2: Update Agent use case."""

from app.layer1_domain.entities.agent import Agent, AgentKind, ConfigType
from app.layer1_domain.exceptions import (
    InvalidDataException,
    InvalidOperationException,
    NotFoundException
)
from app.layer2_application.dtos.update_agent_dto import UpdateAgentDTO
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository
from app.layer2_application.interfaces.tool_repository_port import IToolRepository
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository

from app.layer1_domain.entities.workflow import Workflow
from app.layer2_application.interfaces.fetch_workflow_api_client import IFetchWorkflowApiClient
from app.layer2_application.interfaces.async_executor_port import AsyncExecutorInterface
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger
from app.layer2_application.use_cases.capability_summary_generator import CapabilitySummaryGenerator


class UpdateAgentUseCase:
    """Use case for updating an existing agent.
    
    Business rules:
    - Agent must exist
    - Cannot update status via this use case (use dedicated endpoints)
    - Can update relationships: tools, workflows, agents (child agents)
    """

    def __init__(
        self, 
        repository: IAgentRepository,
        tool_repository: IToolRepository,
        workflow_repository: IWorkflowRepository,
        fetch_workflow_api_client: IFetchWorkflowApiClient,
        async_executor: AsyncExecutorInterface,
        capability_summary_generator: CapabilitySummaryGenerator,
        logger: ILogger | None = None,
    ):

        """Initialize use case with repository dependency.

        Args:
            repository: Agent repository port implementation
            capability_summary_generator: LLM-backed capability summary generator
        """
        self.repository = repository
        self.tool_repository = tool_repository
        self.workflow_repository = workflow_repository
        self.fetch_workflow_api_client = fetch_workflow_api_client
        self.async_executor = async_executor
        self.capability_summary_generator = capability_summary_generator
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(self, agent_id: str, dto: UpdateAgentDTO) -> AgentResponseDTO:
        """Update an existing agent.
        
        Args:
            agent_id: ID of agent to update
            dto: Update agent data transfer object
            
        Returns:
            AgentResponseDTO: Updated agent details
            
        Raises:
            NotFoundException: If agent doesn't exist
            InvalidDataException: If update data is invalid
        """
        # Retrieve existing agent
        agent = await self.async_executor.run_sync(self.repository.find_by_id, agent_id)
        if not agent:
            raise NotFoundException("Agent", entity_id=agent_id)
        
        # Parse enum values if provided
        try:
            kind = AgentKind(dto.kind) if dto.kind is not None else None
            config_type = ConfigType(dto.config_type) if dto.config_type is not None else None
        except ValueError as exc:
            raise InvalidDataException(str(exc)) from exc
            
        # Update capabilities and capability_summary based on new description/relationships
        capabilities, capability_summary = await self._update_capabilities(
            new_description=dto.description,
            new_workflows=dto.workflows,
            new_tools=dto.tools,
            new_agents=dto.agents,
            agent=agent
        )
        
        # Update entity (validates business rules)
        updated = agent.update(
            name=dto.name,
            kind=kind,
            is_published=dto.is_published,
            description=dto.description,
            config_type=config_type,
            tools=dto.tools,  # Update tool relationships
            workflows=dto.workflows,  # Update workflow relationships
            system_prompt=dto.business,
            agents=dto.agents,  # Update child agents
            knowledge_base=dto.knowledge_base,
            model=dto.model,
            provider=dto.provider,  # SA-1938: stored as given, no allowlist
            version=dto.version,
            healthcheck_endpoint=dto.healthcheck_endpoint,
            invoke_endpoint=dto.invoke_endpoint,
            temperature=dto.temperature,
            capabilities=capabilities or [],  # Update capabilities based on tools/workflows
            capability_summary=capability_summary,
            custom_system_prompt=dto.custom_system_prompt,
            custom_instructions=dto.custom_instructions,
            custom_restrictions=dto.custom_restrictions,
            blocked_topics=dto.blocked_topics,
            blocked_keywords=dto.blocked_keywords,
        )
        
        # If no fields were updated, skip persistence and relationship updates
        if not updated:
            return AgentResponseDTO.from_entity(agent)
        
        await self.async_executor.run_sync(self.repository.update,
            agent,
            tool_ids=dto.tools,
            workflow_ids=dto.workflows,
        )
        
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
    
    async def _update_capabilities(
        self,
        new_description: str | None,
        new_workflows: list[str] | None,
        new_tools: list[str] | None,
        new_agents: list[str] | None,
        agent: Agent
        ) -> tuple[list[str], str]:
        """Update agent capabilities and capability_summary based on new description/relationships provided in the update DTO.

        Logic:
            - If workflows/tools/agents are not being updated (i.e. the corresponding field in the DTO is None) or if the new list of workflows/tools/agents is the same as the existing list, then we keep the existing capabilities related to those relationships
            - If workflows/tools/agents are being updated and the new list is different from the existing list, we update the capabilities accordingly
            - capability_summary is regenerated whenever the description changes OR any relationship changes; otherwise it is preserved

        Args:
            new_description: Agent description provided in the update DTO
            new_workflows: List of workflow IDs provided in the update DTO
            new_tools: List of tool IDs provided in the update DTO
            new_agents: List of agent IDs provided in the update DTO
            agent: The agent entity to update capabilities for

        Returns:
            Tuple of (capabilities, capability_summary). Existing values are returned unchanged
            if neither the description nor any relationship changed.
        """
        description_changed = new_description is not None and new_description.strip() != agent.description
        relationships_unchanged = (
            new_workflows == agent.workflows and new_tools == agent.tools and new_agents == agent.agents
        )

        # Return existing capabilities/summary if nothing that feeds them changed
        if relationships_unchanged and not description_changed:
            return agent.capabilities, agent.capability_summary

        # Update capabilities based on workflow
        if new_workflows is None:
            cap_by_workflow = {cap for cap in agent.capabilities if cap in {wf.description for wf in await self.async_executor.run_sync(self.workflow_repository.find_by_ids, agent.workflows)}}
        else:
            cap_by_workflow = set()
            if len(new_workflows) > 0:
                for workflow_id in new_workflows:
                    fetch_workflow = await self._fetch_and_persist_workflow(workflow_id)
                    cap_by_workflow.add(fetch_workflow.description)
        
        # Update capabilities based on tools
        if new_tools is None:
            cap_by_tools = {cap for cap in agent.capabilities if cap in {tool.description for tool in await self.async_executor.run_sync(self.tool_repository.find_by_ids, agent.tools)}}
        else:
            cap_by_tools = set()
            if len(new_tools) > 0:
                tools = await self.async_executor.run_sync(self.tool_repository.find_by_ids, new_tools)
                cap_by_tools.update([tool.description for tool in tools])

        # Update capabilities based on agents
        if new_agents is None:
            cap_by_agents = {cap for cap in agent.capabilities if cap in {ag.description for ag in await self.async_executor.run_sync(self.repository.find_by_ids, agent.agents)}}
        else:
            cap_by_agents = set()
            if len(new_agents) > 0:
                agents = await self.async_executor.run_sync(self.repository.find_by_ids, new_agents)
                cap_by_agents.update([ag.description for ag in agents])
        
        effective_description = new_description.strip() if new_description is not None else agent.description
        capability_summary = await self.capability_summary_generator.generate(
            description=effective_description,
            tool_descriptions=list(cap_by_tools),
            workflow_descriptions=list(cap_by_workflow),
            agent_descriptions=list(cap_by_agents),
        )

        return list(cap_by_workflow | cap_by_tools | cap_by_agents), capability_summary
