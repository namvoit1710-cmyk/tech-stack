"""Unit tests for GetAgentByCriteriaUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent, AgentKind, AgentStatus
from app.layer2_application.use_cases.get_agent_by_criteria import GetAgentByCriteriaUseCase


class TestGetAgentByCriteriaUseCase:
    """Test GetAgentByCriteriaUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAgentByCriteriaUseCase(repository=mock_repository)

    def test_find_by_criteria_all_none(self, use_case, mock_repository):
        """Test finding agents with all criteria as None."""
        mock_repository.find_by_criteria.return_value = []

        result = use_case.execute()

        assert result == []
        mock_repository.find_by_criteria.assert_called_once_with(
            agent_id=None,
            status=None,
            kind=None,
            is_alive=None,
            is_published=None,
        )

    def test_find_by_criteria_with_status(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test finding agents by status."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_criteria.return_value = [agent]

        result = use_case.execute(status=AgentStatus.ACTIVE)

        assert len(result) == 1
        assert result[0].status == AgentStatus.ACTIVE.value
        mock_repository.find_by_criteria.assert_called_once_with(
            agent_id=None,
            status=AgentStatus.ACTIVE,
            kind=None,
            is_alive=None,
            is_published=None,
        )

    def test_find_by_criteria_with_kind(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test finding agents by kind."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_criteria.return_value = [agent]

        result = use_case.execute(kind=AgentKind.BUSINESS)

        assert len(result) == 1
        assert result[0].kind == AgentKind.BUSINESS.value
        mock_repository.find_by_criteria.assert_called_once()

    def test_find_by_criteria_with_is_alive(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test finding agents by is_alive status."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_criteria.return_value = [agent]

        result = use_case.execute(is_alive=True)

        assert len(result) == 1
        mock_repository.find_by_criteria.assert_called_once_with(
            agent_id=None,
            status=None,
            kind=None,
            is_alive=True,
            is_published=None,
        )

    def test_find_by_criteria_with_is_published(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test finding agents by published status."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_criteria.return_value = [agent]

        result = use_case.execute(is_published=True)

        assert len(result) == 1
        mock_repository.find_by_criteria.assert_called_once()

    def test_find_by_criteria_combined(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test finding agents with multiple criteria."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_criteria.return_value = [agent]

        result = use_case.execute(
            status=AgentStatus.ACTIVE,
            kind=AgentKind.BUSINESS,
            is_published=True,
        )

        assert len(result) == 1
        mock_repository.find_by_criteria.assert_called_once_with(
            agent_id=None,
            status=AgentStatus.ACTIVE,
            kind=AgentKind.BUSINESS,
            is_alive=None,
            is_published=True,
        )
