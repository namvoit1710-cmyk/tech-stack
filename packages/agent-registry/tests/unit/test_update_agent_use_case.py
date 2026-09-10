"""Unit tests for UpdateAgentUseCase."""

from unittest.mock import AsyncMock, Mock
from uuid6 import uuid7

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.entities.agent import AgentKind, ConfigType
from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import (
    InvalidDataException,
    InvalidOperationException,
    NotFoundException,
)
from app.layer2_application.dtos.update_agent_dto import UpdateAgentDTO
from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO
from app.layer2_application.use_cases.update_agent import UpdateAgentUseCase


class TestUpdateAgentUseCase:
    """Test UpdateAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        repository = Mock()
        repository.get_tools.return_value = []
        repository.get_workflows.return_value = []
        repository.find_by_ids.return_value = []
        return repository
    
    @pytest.fixture
    def mock_tool_repository(self):
        """Mock tool repository."""
        mock = Mock()
        mock.find_by_ids.return_value = []
        return mock
    
    @pytest.fixture
    def mock_workflow_repository(self):
        """Mock workflow repository."""
        mock = Mock()
        mock.find_by_ids.return_value = []
        return mock

    @pytest.fixture
    def mock_fetch_workflow_api_client(self):
        """Mock workflow fetch client."""
        return AsyncMock()

    @pytest.fixture
    def mock_async_executor(self):
        """Mock async executor that invokes sync functions in-line."""
        executor = Mock()
        async def run_sync(func, *args, **kwargs):
            return func(*args, **kwargs)
        executor.run_sync = run_sync
        return executor

    @pytest.fixture
    def mock_capability_summary_generator(self):
        """Mock CapabilitySummaryGenerator that echoes its inputs, so assertions can check content."""
        generator = Mock()

        async def generate(description, tool_descriptions=None, workflow_descriptions=None, agent_descriptions=None):
            fragments = [description, *(tool_descriptions or []), *(workflow_descriptions or []), *(agent_descriptions or [])]
            non_empty = [f for f in fragments if f]
            return (". ".join(non_empty) + ".") if non_empty else ""

        generator.generate = AsyncMock(side_effect=generate)
        return generator

    @pytest.fixture
    def use_case(
        self,
        mock_repository,
        mock_tool_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        mock_async_executor,
        mock_capability_summary_generator,
    ):
        """Create use case instance."""
        return UpdateAgentUseCase(
            repository=mock_repository,
            tool_repository=mock_tool_repository,
            workflow_repository=mock_workflow_repository,
            fetch_workflow_api_client=mock_fetch_workflow_api_client,
            async_executor=mock_async_executor,
            capability_summary_generator=mock_capability_summary_generator,
        )

    @pytest.fixture
    def update_dto(self):
        """Create update agent DTO."""
        return UpdateAgentDTO(
            name="updated-agent",
            description="Updated description",
            business="Updated prompt",
            is_published=True,
            knowledge_base=["doc-updated"],
            custom_system_prompt="Updated custom prompt",
            custom_instructions=["instruction1"],
            custom_restrictions=["restriction1"],
            blocked_topics=["topic1"],
            blocked_keywords=["keyword1"],
        )

    @pytest.mark.asyncio
    async def test_update_agent_success(
        self, use_case, mock_repository, sample_agent_data, update_dto
    ):
        """Test successfully updating an agent."""
        agent = Agent.create(**sample_agent_data)
        
        # Mock repository
        mock_repository.find_by_id.return_value = agent
        # Execute use case
        result = await use_case.execute(agent.id, update_dto)
        # Assertions
        assert result is not None
        assert result.name == "updated-agent"
        assert result.business == "Updated prompt"
        assert result.knowledge_base == ["doc-updated"]
        assert result.custom_system_prompt == "Updated custom prompt"
        assert result.custom_instructions == ["instruction1"]
        assert result.custom_restrictions == ["restriction1"]
        assert result.blocked_topics == ["topic1"]
        assert result.blocked_keywords == ["keyword1"]
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_agent_not_found(self, use_case, mock_repository, update_dto):
        """Test updating non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            await use_case.execute(agent_id, update_dto)
        
        assert agent_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_agent_duplicate_name_allowed(
        self, use_case, mock_repository, sample_agent_data, update_dto
    ):
        """Test that updating to duplicate name is allowed."""
        agent = Agent.create(**sample_agent_data)
        
        # Create another agent with the target name
        other_agent_data = sample_agent_data.copy()
        other_agent_data["name"] = "updated-agent"
        other_agent = Agent.create(**other_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent
        mock_repository.find_by_name.return_value = other_agent

        result = await use_case.execute(agent.id, update_dto)

        assert result is not None
        mock_repository.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_agent_same_name_allowed(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that keeping the same name is allowed."""
        agent = Agent.create(**sample_agent_data)
        
        # Update DTO with same name
        update_dto = UpdateAgentDTO(
            name="test-agent",  # Same as current name
            description="Updated description",
        )

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case - duplicate-name check should not run
        result = await use_case.execute(agent.id, update_dto)

        # Assertions
        assert result is not None
        # find_by_name should not be called
        mock_repository.find_by_name.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_agent_with_tools(
        self,
        use_case,
        mock_repository,
        mock_tool_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        sample_agent_data,
        sample_tool_data,
        sample_workflow_data,
    ):
        """Test updating relationships and derived capabilities."""
        agent = Agent.create(**sample_agent_data)
        tool_id = str(uuid7())
        workflow_id = str(uuid7())
        child_agent_id = str(uuid7())
        tool = Tool(**sample_tool_data)
        tool.id = tool_id
        tool.description = "Tool description"
        workflow = Workflow(**sample_workflow_data)
        workflow.id = workflow_id
        workflow.description = "Workflow description"
        child_agent = Agent.create(**sample_agent_data)
        child_agent.id = child_agent_id
        child_agent.description = "Child agent description"
        
        update_dto = UpdateAgentDTO(
            tools=[tool_id],
            workflows=[workflow_id],
            agents=[child_agent_id],
            knowledge_base=["doc-9"],
        )

        # Mock repository
        mock_repository.find_by_id.side_effect = lambda requested_id: {
            agent.id: agent,
            child_agent_id: child_agent,
        }.get(requested_id)
        mock_repository.find_by_ids.return_value = [child_agent]
        mock_tool_repository.find_by_id.return_value = tool
        mock_tool_repository.find_by_ids.return_value = [tool]
        mock_workflow_repository.find_by_id.return_value = workflow
        mock_workflow_repository.upsert.return_value = workflow
        mock_fetch_workflow_api_client.fetch_workflow = AsyncMock(
            return_value=WorkflowFetchDTO(
                id=workflow_id, name="test-workflow",
                description="Workflow description", version="1.0.0",
                status="active", main_flow=False,
                input_schema=[], output_schema=[], metadata={}
            )
        )

        # Execute use case
        result = await use_case.execute(agent.id, update_dto)

        # Assertions
        assert result is not None
        assert result.knowledge_base == ["doc-9"]
        assert "Tool description" in result.capabilities
        assert "Workflow description" in result.capabilities
        assert "Child agent description" in result.capabilities
        assert "Tool description" in result.capability_summary
        assert "Workflow description" in result.capability_summary
        assert "Child agent description" in result.capability_summary
        assert agent.description in result.capability_summary
        mock_repository.update.assert_called_once_with(
            agent,
            tool_ids=[tool_id],
            workflow_ids=[workflow_id],
        )
        mock_repository.get_tools.assert_not_called()
        mock_repository.get_workflows.assert_not_called()
        mock_repository.remove_tool.assert_not_called()
        mock_repository.add_tool.assert_not_called()
        mock_repository.remove_workflow.assert_not_called()
        mock_repository.add_workflow.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_agent_description_only_change_regenerates_summary(
        self, use_case, mock_repository, sample_technical_agent_data
    ):
        """Test description-only change regenerates capability_summary even with unchanged (explicitly-empty) relationships."""
        agent_data = sample_technical_agent_data.copy()
        agent_data["capability_summary"] = "A technical agent."
        agent = Agent.create(**agent_data)

        mock_repository.find_by_id.return_value = agent

        update_dto = UpdateAgentDTO(
            description="A rewritten technical agent",
            tools=[],
            workflows=[],
            agents=[],
        )

        result = await use_case.execute(agent.id, update_dto)

        assert result.description == "A rewritten technical agent"
        assert result.capability_summary == "A rewritten technical agent."
        mock_repository.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_agent_no_changes_skips_persistence(
        self, use_case, mock_repository, mock_tool_repository, mock_workflow_repository, sample_agent_data
    ):
        """Test no-op updates do not persist."""
        agent_data = sample_agent_data.copy()
        agent_data["capabilities"] = ["coding"]
        # Must match what CapabilitySummaryGenerator would produce from the mocks below,
        # otherwise recomputing capability_summary looks like a change and breaks the no-op assertion.
        agent_data["capability_summary"] = "A test agent. coding."
        agent = Agent.create(**agent_data)

        mock_repository.find_by_id.return_value = agent
        mock_workflow_repository.find_by_ids.return_value = [
            Mock(description="coding"),
        ]

        result = await use_case.execute(agent.id, UpdateAgentDTO())

        assert result is not None
        mock_repository.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_agent_missing_related_tool_fails(
        self, use_case, mock_repository, mock_tool_repository, sample_agent_data
    ):
        """Test missing related tools still fail validation."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_id.return_value = agent
        mock_tool_repository.find_by_id.return_value = None
        mock_tool_repository.find_by_ids.side_effect = NotFoundException("Tool", entity_id=str(uuid7()))

        with pytest.raises(NotFoundException):
            await use_case.execute(agent.id, UpdateAgentDTO(tools=[str(uuid7())]))

    @pytest.mark.asyncio
    async def test_update_agent_invalid_enum_returns_invalid_data(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test invalid enum inputs are normalized to InvalidDataException."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_id.return_value = agent

        with pytest.raises(InvalidDataException):
            await use_case.execute(
                agent.id,
                UpdateAgentDTO(
                    kind="invalid-kind",
                    config_type=ConfigType.DEFAULT.value,
                    provider="openai",
                ),
            )

        with pytest.raises(InvalidDataException):
            await use_case.execute(
                agent.id,
                UpdateAgentDTO(
                    kind=AgentKind.BUSINESS.value,
                    config_type="invalid-config-type",
                    provider="openai",
                ),
            )

        # SA-1938: provider is no longer an enum, so an arbitrary value is accepted
        # rather than raising. kind and config_type above remain closed enums.
        result = await use_case.execute(
            agent.id,
            UpdateAgentDTO(
                kind=AgentKind.BUSINESS.value,
                config_type=ConfigType.DEFAULT.value,
                provider="aiml",
            ),
        )
        assert result.provider == "aiml"

    @pytest.mark.asyncio
    async def test_update_agent_fails_fast_when_fetched_workflow_cannot_be_saved(
        self,
        use_case,
        mock_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        sample_agent_data,
    ):
        """Test agent update is skipped when importing a fetched workflow fails."""
        agent = Agent.create(**sample_agent_data)
        workflow_id = str(uuid7())
        mock_repository.find_by_id.return_value = agent
        mock_workflow_repository.find_by_id.return_value = None
        mock_fetch_workflow_api_client.fetch_workflow.return_value = WorkflowFetchDTO(
            id=workflow_id,
            name="Fetched workflow",
            description="Fetched workflow description",
            version="1.0.0",
            status="active",
            main_flow=True,
            input_schema=[],
            output_schema=[],
            metadata={},
        )
        mock_workflow_repository.upsert.side_effect = RuntimeError("db write failed")

        with pytest.raises(InvalidOperationException, match="db write failed"):
            await use_case.execute(agent.id, UpdateAgentDTO(workflows=[workflow_id]))

        mock_repository.update.assert_not_called()
