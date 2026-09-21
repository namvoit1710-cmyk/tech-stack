"""Gap-fill unit tests for RegisterAgentUseCase.

Targets uncovered branches in
``app/layer2_application/use_cases/register_agent.py`` surfaced by the
2026-07-02 coverage sweep (lines 85-88, 93-95, 99-101, 105-107, 160-171,
184-188, 204-209). Expectations grounded in the source, not convention.
Mock idiom copied from ``test_register_agent_use_case.py`` (sync repos =
``Mock``; async API clients = ``AsyncMock``; ``async_executor.run_sync``
invokes the sync callable in-line).
"""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from uuid6 import uuid7

from app.layer1_domain.exceptions import (
    AlreadyExistsException,
    InvalidDataException,
    InvalidOperationException,
)
from app.layer2_application.dtos.register_agent_dto import RegisterAgentDTO
from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO
from app.layer2_application.use_cases.register_agent import RegisterAgentUseCase


class TestRegisterAgentUseCaseGapFill:
    """Cover the uncovered register branches."""

    @pytest.fixture
    def mock_settings(self):
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
        repo = Mock()
        repo.find_by_name_and_version.return_value = None  # SA-1072/#9: default = no duplicate
        return repo

    @pytest.fixture
    def mock_tool_repository(self):
        return Mock()

    @pytest.fixture
    def mock_workflow_repository(self):
        return Mock()

    @pytest.fixture
    def mock_fetch_workflow_api_client(self):
        client = Mock()
        client.fetch_workflow = AsyncMock()
        return client

    @pytest.fixture
    def mock_fetch_current_user_info_api_client(self):
        client = Mock()
        client.fetch_current_user_info = AsyncMock()
        return client

    @pytest.fixture
    def mock_uuid_generator(self):
        generator = Mock()
        generator.generate_uuid.side_effect = [str(uuid7()) for _ in range(10)]
        return generator

    @pytest.fixture
    def mock_async_executor(self):
        executor = Mock()

        async def run_sync(func, *args, **kwargs):
            return func(*args, **kwargs)

        executor.run_sync = run_sync
        return executor

    @pytest.fixture
    def mock_user_repository(self):
        return Mock()

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
        )

    # --- invalid input: enum parse failures (93-95, 99-101, 105-107) ----------
    async def test_register_invalid_kind_raises_invalid_data(self, use_case, register_dto):
        """Unknown kind string -> InvalidDataException listing supported kinds."""
        with pytest.raises(InvalidDataException) as exc:
            await use_case.execute(replace(register_dto, kind="wizard"))
        assert "Invalid agent kind" in str(exc.value)

    async def test_register_invalid_status_raises_invalid_data(self, use_case, register_dto):
        """Unknown status string -> InvalidDataException listing supported statuses."""
        with pytest.raises(InvalidDataException) as exc:
            await use_case.execute(replace(register_dto, status="sleeping"))
        assert "Invalid agent status" in str(exc.value)

    async def test_register_invalid_config_type_raises_invalid_data(
        self, use_case, register_dto
    ):
        """Unknown config_type string -> InvalidDataException listing supported types."""
        with pytest.raises(InvalidDataException) as exc:
            await use_case.execute(replace(register_dto, config_type="turbo"))
        assert "Invalid config type" in str(exc.value)

    # --- failure path: save raises (160-171) ----------------------------------
    async def test_register_reraises_already_exists_from_save(
        self, use_case, mock_agent_repository, register_dto
    ):
        """AlreadyExistsException from save is re-raised unchanged (not wrapped)."""
        mock_agent_repository.save.side_effect = AlreadyExistsException("Agent", "test-agent")
        with pytest.raises(AlreadyExistsException):
            await use_case.execute(register_dto)

    async def test_register_wraps_generic_save_error_as_invalid_data(
        self, use_case, mock_agent_repository, register_dto
    ):
        """A generic save error is wrapped as InvalidDataException with the agent id."""
        mock_agent_repository.save.side_effect = RuntimeError("db exploded")
        with pytest.raises(InvalidDataException) as exc:
            await use_case.execute(register_dto)
        assert "Failed to save agent" in str(exc.value)

    # --- edge: workflow already exists w/ same content -> skip upsert (184-188)
    async def test_register_skips_workflow_upsert_when_content_unchanged(
        self,
        use_case,
        mock_agent_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        register_dto,
        sample_workflow_data,
    ):
        """Existing workflow with equal content -> upsert is NOT called; capability still added."""
        from app.layer1_domain.entities.workflow import Workflow

        workflow_id = sample_workflow_data["id"]
        existing = Workflow(**sample_workflow_data)
        mock_agent_repository.save.return_value = None
        mock_workflow_repository.find_by_id.return_value = existing
        # Fetched DTO must map to a content-equal Workflow entity.
        mock_fetch_workflow_api_client.fetch_workflow = AsyncMock(
            return_value=WorkflowFetchDTO(
                id=workflow_id,
                name=existing.name,
                description=existing.description,
                version=existing.version,
                status=existing.status,
                main_flow=existing.main_flow,
                input_schema=existing.input_schema,
                output_schema=existing.output_schema,
                metadata=existing.metadata,
            )
        )

        result = await use_case.execute(replace(register_dto, workflows=[workflow_id]))

        assert result is not None
        mock_workflow_repository.upsert.assert_not_called()
        saved_agent = mock_agent_repository.save.call_args.args[0]
        assert existing.description in saved_agent.capabilities

    # --- failure path: workflow upsert fails -> InvalidOperationException (204-209)
    async def test_register_wraps_workflow_upsert_failure(
        self,
        use_case,
        mock_agent_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        register_dto,
        sample_workflow_data,
    ):
        """Upsert raising -> InvalidOperationException surfaced from _fetch_and_persist_workflow."""
        workflow_id = sample_workflow_data["id"]
        mock_agent_repository.save.return_value = None
        # No existing workflow, so it proceeds to upsert.
        mock_workflow_repository.find_by_id.return_value = None
        mock_workflow_repository.upsert.side_effect = RuntimeError("upsert failed")
        mock_fetch_workflow_api_client.fetch_workflow = AsyncMock(
            return_value=WorkflowFetchDTO(
                id=workflow_id,
                name="wf",
                description="wf desc",
                version="1.0.0",
                status="active",
                main_flow=False,
                input_schema=[],
                output_schema=[],
                metadata={},
            )
        )

        with pytest.raises(InvalidOperationException) as exc:
            await use_case.execute(replace(register_dto, workflows=[workflow_id]))
        assert "Failed to upsert workflow" in str(exc.value)
