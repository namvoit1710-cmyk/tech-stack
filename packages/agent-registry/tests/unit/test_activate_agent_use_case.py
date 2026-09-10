"""Unit tests for ActivateAgentUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent, AgentStatus
from app.layer1_domain.exceptions import (
    NotFoundException,
    InvalidOperationException,
)
from app.layer2_application.use_cases.activate_agent import ActivateAgentUseCase


class TestActivateAgentUseCase:
    """Test ActivateAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return ActivateAgentUseCase(repository=mock_repository)

    def test_activate_agent_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully activating a published agent."""
        # Create agent with inactive status but published
        sample_agent_data["status"] = AgentStatus.INACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions
        assert result is not None
        assert result.status == AgentStatus.ACTIVE.value
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.update.assert_called_once()

    def test_activate_agent_not_found(self, use_case, mock_repository):
        """Test activating non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_activate_agent_not_published(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that unpublished agent cannot be activated."""
        # Create agent that is not published
        sample_agent_data["is_published"] = False
        sample_agent_data["status"] = AgentStatus.INACTIVE
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute and assert exception
        with pytest.raises(InvalidOperationException) as exc_info:
            use_case.execute(agent.id)
        
        assert "must be published first" in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_activate_already_active_agent(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test activating an already active agent."""
        # Create agent that is already active and published
        sample_agent_data["status"] = AgentStatus.ACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - should succeed but no update needed
        assert result is not None
        assert result.status == AgentStatus.ACTIVE.value
        # update() should return False since no change
        mock_repository.update.assert_not_called()
