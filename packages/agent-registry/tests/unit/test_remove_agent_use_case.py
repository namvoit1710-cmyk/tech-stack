"""Unit tests for RemoveAgentUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.remove_agent import RemoveAgentUseCase


class TestRemoveAgentUseCase:
    """Test RemoveAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return RemoveAgentUseCase(repository=mock_repository)

    def test_remove_agent_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully removing an agent."""
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        use_case.execute(agent.id)

        # Assertions
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.soft_delete.assert_called_once_with(agent.id)

    def test_remove_agent_not_found(self, use_case, mock_repository):
        """Test removing non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)
        mock_repository.soft_delete.assert_not_called()
