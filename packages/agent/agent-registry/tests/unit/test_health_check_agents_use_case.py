"""Unit tests for HealthCheckAgentsUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer2_application.use_cases.health_check_agents import HealthCheckAgentsUseCase


class TestHealthCheckAgentsUseCase:
    """Test periodic health check behavior."""

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
    async def test_execute_updates_agents_from_batch_health_check(
        self,
        use_case,
        mock_repository,
        mock_health_check_client,
        sample_technical_agent_data,
    ):
        agent = Agent.create(**sample_technical_agent_data)
        mock_repository.find_all.return_value = [agent]
        mock_health_check_client.batch_check_health.return_value = {
            agent.healthcheck_endpoint: True,
        }

        await use_case.execute()

        mock_repository.find_all.assert_called_once_with()
        mock_health_check_client.batch_check_health.assert_awaited_once_with(
            [agent.healthcheck_endpoint]
        )
        assert agent.is_alive is True
        assert agent.last_health_check_at is not None
        mock_repository.update_health_status.assert_called_once_with(
            agent.id, agent.is_alive, agent.last_health_check_at
        )
        mock_repository.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_skips_batch_check_when_no_health_checkable_agents(
        self,
        use_case,
        mock_repository,
        mock_health_check_client,
    ):
        mock_repository.find_all.return_value = []

        await use_case.execute()

        mock_repository.find_all.assert_called_once_with()
        mock_health_check_client.batch_check_health.assert_not_awaited()
        mock_repository.update.assert_not_called()
        mock_repository.update_health_status.assert_not_called()