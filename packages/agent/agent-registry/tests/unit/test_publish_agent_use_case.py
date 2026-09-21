"""Unit tests for PublishAgentUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.publish_agent import PublishAgentUseCase


class TestPublishAgentUseCase:
    """Test PublishAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):  
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return PublishAgentUseCase(repository=mock_repository)

    def test_publish_agent_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully publishing an unpublished agent."""
        # Create unpublished agent
        sample_agent_data["is_published"] = False
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions
        assert result is not None
        assert result.is_published is True
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.update.assert_called_once()

    def test_publish_agent_not_found(self, use_case, mock_repository):
        """Test publishing non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_publish_already_published_agent(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test publishing an already published agent."""
        # Create already published agent
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - should succeed but no update needed
        assert result is not None
        assert result.is_published is True
