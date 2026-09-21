"""Unit tests for UnpublishAgentUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent, AgentStatus
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.unpublish_agent import UnpublishAgentUseCase


class TestUnpublishAgentUseCase:
    """Test UnpublishAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return UnpublishAgentUseCase(repository=mock_repository)

    def test_unpublish_agent_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully unpublishing a published agent."""
        # Create published agent
        sample_agent_data["is_published"] = True
        sample_agent_data["status"] = AgentStatus.ACTIVE
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions
        assert result is not None
        assert result.is_published is False
        assert result.status == AgentStatus.INACTIVE.value  # Must be inactive when unpublished
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.update.assert_called_once()

    def test_unpublish_agent_not_found(self, use_case, mock_repository):
        """Test unpublishing non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_unpublish_already_unpublished_agent(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test unpublishing an already unpublished agent."""
        # Create already unpublished agent
        sample_agent_data["is_published"] = False
        sample_agent_data["status"] = AgentStatus.INACTIVE
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - should succeed but no update needed
        assert result is not None
        assert result.is_published is False
        assert result.status == AgentStatus.INACTIVE.value

    def test_unpublish_sets_status_to_inactive(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that unpublishing sets status to inactive."""
        # Create published and active agent
        sample_agent_data["is_published"] = True
        sample_agent_data["status"] = AgentStatus.ACTIVE
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - both flags should change
        assert result.is_published is False
        assert result.status == AgentStatus.INACTIVE.value
        mock_repository.update.assert_called_once()
