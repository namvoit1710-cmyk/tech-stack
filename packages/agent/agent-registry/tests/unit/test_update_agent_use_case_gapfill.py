"""Gap-fill unit tests for UpdateAgentUseCase.

Targets uncovered branches in
``app/layer2_application/use_cases/update_agent.py`` from the 2026-07-02
sweep: line 178 (``_update_capabilities`` short-circuit when no relationship
field changed) and lines 129-133 (``_fetch_and_persist_workflow`` skip-upsert
when the fetched workflow content already matches the stored one).
Mock idiom copied from ``test_update_agent_use_case.py``.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.entities.workflow import Workflow
from app.layer2_application.dtos.update_agent_dto import UpdateAgentDTO
from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO
from app.layer2_application.use_cases.update_agent import UpdateAgentUseCase


class TestUpdateAgentUseCaseGapFill:
    """Cover the uncovered update branches."""

    @pytest.fixture
    def mock_repository(self):
        repository = Mock()
        repository.find_by_ids.return_value = []
        return repository

    @pytest.fixture
    def mock_tool_repository(self):
        mock = Mock()
        mock.find_by_ids.return_value = []
        return mock

    @pytest.fixture
    def mock_workflow_repository(self):
        mock = Mock()
        mock.find_by_ids.return_value = []
        return mock

    @pytest.fixture
    def mock_fetch_workflow_api_client(self):
        return AsyncMock()

    @pytest.fixture
    def mock_async_executor(self):
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
        return UpdateAgentUseCase(
            repository=mock_repository,
            tool_repository=mock_tool_repository,
            workflow_repository=mock_workflow_repository,
            fetch_workflow_api_client=mock_fetch_workflow_api_client,
            async_executor=mock_async_executor,
            capability_summary_generator=mock_capability_summary_generator,
        )

    # --- edge: no relationship change -> keep existing capabilities (line 178) -
    async def test_update_keeps_existing_capabilities_when_relationships_unchanged(
        self,
        use_case,
        mock_repository,
        mock_tool_repository,
        mock_workflow_repository,
        sample_agent_data,
    ):
        """DTO repeats the agent's existing tools/workflows/agents verbatim ->
        _update_capabilities returns agent.capabilities without touching repos."""
        agent = Agent.create(**sample_agent_data)
        agent.capabilities = ["cap-from-tool", "cap-from-workflow"]
        mock_repository.find_by_id.return_value = agent

        # Force a top-level update via a non-capability field (name) while leaving the
        # description AND every relationship list unchanged. Per SA-1356 the capability
        # summary regenerates whenever the description OR a relationship changes; since
        # neither changed here, _update_capabilities short-circuits -> capabilities are
        # kept and no relationship repos are queried.
        dto = UpdateAgentDTO(
            name="a fresh name to force an update",
            tools=agent.tools,
            workflows=agent.workflows,
            agents=agent.agents,
        )

        result = await use_case.execute(agent.id, dto)

        assert result is not None
        # Existing capabilities preserved unchanged.
        assert sorted(result.capabilities) == sorted(["cap-from-tool", "cap-from-workflow"])
        # No relationship lookups happened (short-circuit taken).
        mock_tool_repository.find_by_ids.assert_not_called()
        mock_workflow_repository.find_by_ids.assert_not_called()

    # --- edge: fetched workflow content unchanged -> skip upsert (129-133) -----
    async def test_update_skips_workflow_upsert_when_content_unchanged(
        self,
        use_case,
        mock_repository,
        mock_workflow_repository,
        mock_fetch_workflow_api_client,
        sample_agent_data,
        sample_workflow_data,
    ):
        """A newly-added workflow whose fetched content equals the stored one ->
        workflow_repository.upsert is NOT called; its description becomes a capability."""
        agent = Agent.create(**sample_agent_data)
        agent.capabilities = []
        mock_repository.find_by_id.return_value = agent

        workflow_id = sample_workflow_data["id"]
        existing = Workflow(**sample_workflow_data)
        mock_workflow_repository.find_by_id.return_value = existing
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

        # Add a NEW workflow (different from agent.workflows) to force the
        # capability-recompute path, keep tools/agents unchanged.
        dto = UpdateAgentDTO(
            workflows=[workflow_id],
            tools=agent.tools,
            agents=agent.agents,
        )

        result = await use_case.execute(agent.id, dto)

        assert result is not None
        mock_workflow_repository.upsert.assert_not_called()
        assert existing.description in result.capabilities
