"""Unit tests for RegisterAgentUseCase."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid6 import uuid7

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import (
    AlreadyExistsException,
    EndpointRequiredException,
    InvalidDataException,
)
from app.layer2_application.dtos.register_agent_dto import RegisterAgentDTO
from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO
from app.layer2_application.use_cases.register_agent import RegisterAgentUseCase


class TestRegisterAgentUseCase:
    """Test RegisterAgentUseCase."""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock settings."""
        settings = Mock()
        settings.default_provider = "openai"
        settings.default_model = "gpt-4.1"
        settings.default_temperature = 0.4
        settings.default_max_tokens = 2048
        settings.default_timeout_ms = 15000
        settings.default_max_concurrency = 3
        settings.default_retry_count = 2
        settings.default_streaming_supported = True
        return settings

    @pytest.fixture
    def mock_agent_repository(self):
        """Mock agent repository."""
        repo = Mock()
        repo.find_by_name_and_version.return_value = None  # SA-1072/#9: default = no duplicate
        return repo

    @pytest.fixture
    def mock_tool_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def mock_workflow_repository(self):
        """Mock workflow repository."""
        return Mock()

    @pytest.fixture
    def mock_user_repository(self):
        """Mock user repository."""
        return Mock()

    @pytest.fixture
    def mock_fetch_workflow_api_client(self):
        """Mock workflow fetch API client."""
        client = Mock()
        client.fetch_workflow = AsyncMock()
        return client

    @pytest.fixture
    def mock_fetch_current_user_info_api_client(self):
        """Mock current user info API client."""
        client = Mock()
        client.fetch_current_user_info = AsyncMock()
        client.fetch_current_user_groups = AsyncMock(return_value=[])
        client.fetch_current_user_group_detail = AsyncMock()
        return client

    @pytest.fixture
    def mock_uuid_generator(self):
        """Mock UUID generator."""
        generator = Mock()
        generator.generate_uuid.side_effect = [str(uuid7()) for _ in range(10)]
        return generator

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
        mock_agent_repository,
        mock_tool_repository,
        mock_workflow_repository,
        mock_user_repository,
        mock_fetch_workflow_api_client,
        mock_fetch_current_user_info_api_client,
        mock_settings,
        mock_uuid_generator,
        mock_async_executor,
        mock_capability_summary_generator,
    ):
        """Create use case instance."""
        return RegisterAgentUseCase(
            repository=mock_agent_repository,
            tool_repository=mock_tool_repository,
            workflow_repository=mock_workflow_repository,
            fetch_workflow_api_client=mock_fetch_workflow_api_client,
            settings=mock_settings,
            uuid_generator=mock_uuid_generator,
            async_executor=mock_async_executor,
            capability_summary_generator=mock_capability_summary_generator,
        )

    @pytest.fixture
    def register_dto(self):
        """Create register agent DTO."""
        return RegisterAgentDTO(
            name="test-agent",
            kind="business",
            status="active",
            description="A test agent",
            provider="openai",
            model="gpt-4",
            temperature=0.7,
            business="You are helpful",
            config_type="default",
            version="1.0.0",
            is_published=False,
            healthcheck_endpoint=None,
            invoke_endpoint="https://agent.example.com/invoke",
            tools=[],
            workflows=[],
            agents=[],
            knowledge_base=[],
            custom_system_prompt="Custom system prompt",
            custom_instructions=["instruction1"],
            custom_restrictions=["restriction1"],
            blocked_topics=["topic1"],
            blocked_keywords=["keyword1"],
        )

    async def test_register_agent_success(self, use_case, mock_agent_repository, register_dto):
        """Test successful agent registration."""
        # Mock repository responses
        mock_agent_repository.save.return_value = None

        # Execute use case
        result = await use_case.execute(register_dto)

        # Assertions
        assert result is not None
        assert result.name == "test-agent"
        assert result.business == "You are helpful"
        mock_agent_repository.save.assert_called_once()
        assert mock_agent_repository.save.call_args.kwargs == {
            "tool_ids": [],
            "workflow_ids": [],
        }

        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert saved_agent.knowledge_base == []
        assert saved_agent.max_tokens == 2048
        assert saved_agent.user_email is None
        assert saved_agent.tenant_id is None
        assert saved_agent.custom_system_prompt == "Custom system prompt"
        assert saved_agent.custom_instructions == ["instruction1"]
        assert saved_agent.custom_restrictions == ["restriction1"]
        assert saved_agent.blocked_topics == ["topic1"]
        assert saved_agent.blocked_keywords == ["keyword1"]

    async def test_register_agent_does_not_populate_user_context(
        self,
        use_case,
        mock_agent_repository,
        register_dto,
    ):
        """T5: register no longer derives creator identity — user_email/tenant_id are None."""
        mock_agent_repository.save.return_value = None
        result = await use_case.execute(register_dto, token="Bearer token")
        assert result.user_email is None and result.tenant_id is None
        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert saved_agent.user_email is None and saved_agent.tenant_id is None

    async def test_register_agent_duplicate_name_allowed(
        self, use_case, mock_agent_repository, register_dto, sample_agent_data
    ):
        """Test that registering agent with duplicate name is allowed."""
        existing_agent = Agent.create(**sample_agent_data)
        mock_agent_repository.find_by_name.return_value = existing_agent
        mock_agent_repository.save.return_value = None

        result = await use_case.execute(register_dto)

        assert result is not None
        mock_agent_repository.save.assert_called_once()

    async def test_register_agent_with_relationships_and_knowledge_base(
        self,
        use_case,
        mock_agent_repository,
        mock_tool_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        register_dto,
        sample_agent_data,
        sample_tool_data,
        sample_workflow_data,
    ):
        """Test registering agent with related entities and knowledge base."""
        tool_id = str(uuid7())
        workflow_id = str(uuid7())
        child_agent_id = str(uuid7())
        dto = replace(
            register_dto,
            tools=[tool_id],
            workflows=[workflow_id],
            agents=[child_agent_id],
            knowledge_base=["doc-1", "doc-2"],
        )

        # Mock repositories
        mock_agent_repository.save.return_value = None
        tool = Tool(**sample_tool_data)
        tool.id = tool_id
        workflow = Workflow(**sample_workflow_data)
        workflow.id = workflow_id
        child_agent = Agent.create(**sample_agent_data)
        child_agent.id = child_agent_id
        child_agent.description = "Child agent description"
        mock_tool_repository.find_by_ids.return_value = [tool]
        mock_workflow_repository.find_by_id.return_value = workflow
        mock_workflow_repository.upsert.return_value = workflow
        mock_agent_repository.find_by_ids.return_value = [child_agent]
        mock_fetch_workflow_api_client.fetch_workflow = AsyncMock(
            return_value=WorkflowFetchDTO(
                id=workflow_id, name="test-workflow",
                description="A test workflow", version="1.0.0",
                status="active", main_flow=False,
                input_schema=[], output_schema=[], metadata={}
            )
        )

        # Execute use case
        result = await use_case.execute(dto)

        # Assertions
        assert result is not None 
        assert sorted(result.knowledge_base) == sorted(["doc-1", "doc-2"])
        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert sorted(saved_agent.knowledge_base) == sorted(["doc-1", "doc-2"])
        assert tool.description in saved_agent.capabilities
        assert workflow.description in saved_agent.capabilities
        assert child_agent.description in saved_agent.capabilities
        assert dto.description in saved_agent.capability_summary
        assert tool.description in saved_agent.capability_summary
        assert workflow.description in saved_agent.capability_summary
        assert child_agent.description in saved_agent.capability_summary
        mock_tool_repository.find_by_ids.assert_called_once_with([tool_id])
        mock_workflow_repository.find_by_id.assert_called_once_with(workflow_id)
        mock_agent_repository.find_by_ids.assert_called_once_with([child_agent_id])

    async def test_register_agent_calls_capability_summary_generator_with_all_sources(
        self,
        use_case,
        mock_agent_repository,
        mock_tool_repository,
        mock_capability_summary_generator,
        register_dto,
        sample_tool_data,
    ):
        """Test the use case delegates summary generation to the injected generator with all sources."""
        tool_id = str(uuid7())
        dto = replace(register_dto, description="Shared capability text", tools=[tool_id])

        tool = Tool(**sample_tool_data)
        tool.id = tool_id
        tool.description = "Tool capability text"
        mock_tool_repository.find_by_ids.return_value = [tool]
        mock_agent_repository.save.return_value = None

        result = await use_case.execute(dto)

        mock_capability_summary_generator.generate.assert_awaited_once_with(
            description="Shared capability text",
            tool_descriptions=["Tool capability text"],
            workflow_descriptions=[],
            agent_descriptions=[],
        )
        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert result.capability_summary == saved_agent.capability_summary
        assert "Shared capability text" in saved_agent.capability_summary
        assert "Tool capability text" in saved_agent.capability_summary

    async def test_register_agent_uses_settings_defaults_when_values_omitted(
        self, use_case, mock_agent_repository, register_dto
    ):
        """Test omitted create-time fields fall back to settings.

        SA-1938 exception: `provider`, `model` and `temperature` have NO fallback. They
        are the caller's choice, so an omitted value stays None rather than being
        replaced by one the caller never picked. The operational knobs below
        (max_tokens, timeout_ms, ...) keep their settings-driven defaults.
        """
        dto = replace(
            register_dto,
            provider=None,
            model=None,
            temperature=None,
            max_tokens=None,
            timeout_ms=None,
            max_concurrency=None,
            retry_count=None,
            streaming_supported=None,
        )
        mock_agent_repository.save.return_value = None

        result = await use_case.execute(dto)

        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert result is not None
        # SA-1938: the caller's three fields stay unset - no default is injected.
        assert saved_agent.provider is None
        assert saved_agent.model is None
        assert saved_agent.temperature is None
        assert saved_agent.max_tokens == 2048
        assert saved_agent.timeout_ms == 15000
        assert saved_agent.max_concurrency == 3
        assert saved_agent.retry_count == 2
        assert saved_agent.streaming_supported is True

    async def test_register_technical_agent_with_endpoint(
        self, use_case, mock_agent_repository, register_dto
    ):
        """Test registering technical agent with healthcheck endpoint."""
        technical_dto = replace(
            register_dto,
            kind="technical",
            healthcheck_endpoint="https://agent.example.com/health",
        )

        # Execute use case
        result = await use_case.execute(technical_dto)

        # Assertions
        assert result is not None
        assert result.kind == "technical"
        assert result.healthcheck_endpoint == "https://agent.example.com/health"

    async def test_register_technical_agent_without_endpoint_keeps_endpoint_empty(
        self, use_case, mock_agent_repository, register_dto
    ):
        """Test that registering technical agent without endpoint is allowed."""
        technical_dto = replace(register_dto, kind="technical", healthcheck_endpoint=None)

        result = await use_case.execute(technical_dto)

        assert result is not None
        assert result.kind == "technical"
        assert result.healthcheck_endpoint is None

    async def test_register_agent_accepts_any_provider(
        self, use_case, mock_agent_repository, register_dto
    ):
        """SA-1938: the FE chooses the provider; the registry stores it verbatim.

        'aiml' is a real LiteLLM provider that used to be rejected by the old
        single-value LLMProvider enum.
        """
        mock_agent_repository.save.return_value = None

        result = await use_case.execute(replace(register_dto, provider="aiml"))

        assert result.provider == "aiml"

    # --- SA-963: capability building when a workflow's name/description are missing ---
    # These cover the _resolve_workflow_capabilities branches (pure sync helper) that
    # previously had no direct test:
    #   * skip a workflow that has neither a name nor a description
    #   * fall back to the name when the description is missing
    #   * (regression) prefer the description when present

    def test_sync_capabilities_skips_workflow_with_no_name_or_description(self, use_case):
        """SA-963: a workflow with neither name nor description is skipped entirely."""
        workflow = Workflow.create(
            id=str(uuid7()), name="", description="", version="1.0.0", status="active",
        )
        assert use_case._resolve_workflow_capabilities([workflow]) == []

    def test_sync_capabilities_skips_workflow_with_whitespace_name_and_description(self, use_case):
        """SA-963: whitespace-only name + description counts as 'neither' and is skipped."""
        workflow = Workflow.create(
            id=str(uuid7()), name="   ", description="   ", version="1.0.0", status="active",
        )
        assert use_case._resolve_workflow_capabilities([workflow]) == []

    def test_sync_capabilities_uses_name_when_description_missing(self, use_case):
        """SA-963: a workflow with a name but no description contributes its name."""
        workflow = Workflow.create(
            id=str(uuid7()), name="MyFlow", description="", version="1.0.0", status="active",
        )
        assert use_case._resolve_workflow_capabilities([workflow]) == ["MyFlow"]

    def test_sync_capabilities_prefers_description_over_name(self, use_case):
        """A workflow with a description contributes the description (name is not used)."""
        workflow = Workflow.create(
            id=str(uuid7()), name="MyFlow", description="Real description",
            version="1.0.0", status="active",
        )
        assert use_case._resolve_workflow_capabilities([workflow]) == ["Real description"]

    def test_sync_capabilities_mixed_workflows_resolve_independently(self, use_case):
        """SA-963: in a mixed list each workflow resolves independently; order is preserved."""
        skipped = Workflow.create(
            id=str(uuid7()), name="", description="", version="1.0.0", status="active",
        )
        named = Workflow.create(
            id=str(uuid7()), name="NamedFlow", description=None, version="1.0.0", status="active",
        )
        described = Workflow.create(
            id=str(uuid7()), name="X", description="Described", version="1.0.0", status="active",
        )
        assert use_case._resolve_workflow_capabilities(
            [skipped, named, described]
        ) == ["NamedFlow", "Described"]

    async def test_register_rejects_duplicate_name_and_version_before_side_effects(
        self, use_case, mock_agent_repository, mock_workflow_repository, register_dto, sample_agent_data
    ):
        """SA-1072/#9: a duplicate (name, version) is rejected BEFORE any workflow is
        persisted and before the agent save, so no orphaned workflow rows are left behind."""
        mock_agent_repository.find_by_name_and_version.return_value = Agent.create(**sample_agent_data)

        with pytest.raises(AlreadyExistsException):
            await use_case.execute(register_dto)

        # fail-fast: neither the workflow upsert nor the agent save should have run
        mock_workflow_repository.upsert.assert_not_called()
        mock_agent_repository.save.assert_not_called()
