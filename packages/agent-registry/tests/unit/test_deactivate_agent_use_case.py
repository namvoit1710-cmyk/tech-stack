"""Unit tests for DeactivateAgentUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent, AgentStatus
from app.layer1_domain.exceptions import (
    NotFoundException,
    InvalidOperationException,
)
from app.layer2_application.use_cases.deactivate_agent import DeactivateAgentUseCase


class TestDeactivateAgentUseCase:
    """Test DeactivateAgentUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return DeactivateAgentUseCase(repository=mock_repository)

    def test_deactivate_agent_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully deactivating a published agent."""
        # Create agent with active status and published
        sample_agent_data["status"] = AgentStatus.ACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions
        assert result is not None
        assert result.status == AgentStatus.INACTIVE.value
        mock_repository.find_by_id.assert_called_once_with(agent.id)
        mock_repository.update.assert_called_once()

    def test_deactivate_agent_not_found(self, use_case, mock_repository):
        """Test deactivating non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_deactivate_agent_not_published(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that unpublished agent cannot be deactivated."""
        # Create agent that is not published
        sample_agent_data["is_published"] = False
        sample_agent_data["status"] = AgentStatus.ACTIVE
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute and assert exception
        with pytest.raises(InvalidOperationException) as exc_info:
            use_case.execute(agent.id)
        
        assert "must be published" in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_deactivate_already_inactive_agent(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test deactivating an already inactive agent."""
        # Create agent that is already inactive but published
        sample_agent_data["status"] = AgentStatus.INACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - should succeed but no update needed
        assert result is not None
        assert result.status == AgentStatus.INACTIVE.value
        mock_repository.update.assert_not_called()
