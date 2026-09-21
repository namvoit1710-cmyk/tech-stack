"""Unit tests for GetAllAgentsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent
from app.layer2_application.use_cases.get_all_agents import GetAllAgentsUseCase


class TestGetAllAgentsUseCase:
    """Test GetAllAgentsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAllAgentsUseCase(repository=mock_repository)

    def test_get_all_agents_empty(self, use_case, mock_repository):
        """Test getting all agents when none exist."""
        # Mock repository to return empty list
        mock_repository.find_all.return_value = []

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert result == []
        mock_repository.find_all.assert_called_once()

    def test_get_all_agents_with_data(
        self, use_case, mock_repository, sample_agent_data, sample_technical_agent_data
    ):
        """Test getting all agents when multiple exist."""
        # Create sample agents
        agent1 = Agent.create(**sample_agent_data)
        agent2 = Agent.create(**sample_technical_agent_data)
        
        # Mock repository to return agents
        mock_repository.find_all.return_value = [agent1, agent2]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 2
        assert result[0].name == "test-agent"
        assert result[1].name == "technical-agent"
        mock_repository.find_all.assert_called_once()

    def test_get_all_agents_returns_dtos(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that get all agents returns DTOs not entities."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions - result should be DTO, not entity
        assert len(result) == 1
        from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
        assert isinstance(result[0], AgentResponseDTO)
