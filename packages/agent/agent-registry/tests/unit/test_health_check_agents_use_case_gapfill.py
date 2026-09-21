"""Gap-fill unit test for HealthCheckAgentsUseCase.

Targets uncovered lines 62-63 in
``app/layer2_application/use_cases/health_check_agents.py`` (the ``is_alive``
False branch -> ``agent.mark_dead()`` + persist). Mock idiom copied from
``test_health_check_agents_use_case.py``.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer2_application.use_cases.health_check_agents import HealthCheckAgentsUseCase


class TestHealthCheckAgentsUseCaseGapFill:
    """Cover the failed-health-check (mark_dead) branch."""

    @pytest.fixture
    def mock_repository(self):
        return Mock()

    @pytest.fixture
    def mock_health_check_client(self):
        client = Mock()
        client.batch_check_health = AsyncMock()
        return client

    @pytest.fixture
    def mock_async_executor(self):
        executor = Mock()

        async def run_sync(func, *args, **kwargs):
            return func(*args, **kwargs)

        executor.run_sync = run_sync
        return executor

    @pytest.fixture
    def use_case(self, mock_repository, mock_health_check_client, mock_async_executor):
        return HealthCheckAgentsUseCase(
            agent_repository=mock_repository,
            health_check_client=mock_health_check_client,
            async_executor=mock_async_executor,
        )

    @pytest.mark.asyncio
    async def test_execute_marks_agent_dead_on_failed_health_check(
        self,
        use_case,
        mock_repository,
        mock_health_check_client,
        sample_technical_agent_data,
    ):
        """A technical agent reporting is_alive=False is marked dead and persisted."""
        agent = Agent.create(**sample_technical_agent_data)
        agent.is_alive = True  # start alive so the transition is observable
        mock_repository.find_all.return_value = [agent]
        mock_health_check_client.batch_check_health.return_value = {
            agent.healthcheck_endpoint: False,
        }

        await use_case.execute()

        assert agent.is_alive is False
        assert agent.last_health_check_at is not None
        mock_repository.update_health_status.assert_called_once_with(
            agent.id, False, agent.last_health_check_at
        )
