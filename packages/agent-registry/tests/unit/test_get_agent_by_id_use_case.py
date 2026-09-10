"""Unit tests for GetAgentByIdUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.get_agent_by_id import GetAgentByIdUseCase


class TestGetAgentByIdUseCase:
    """Test GetAgentByIdUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAgentByIdUseCase(repository=mock_repository)

    def test_get_agent_by_id_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully getting an agent by ID."""
        agent = Agent.create(**sample_agent_data)

        # Mock repository
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions
        assert result is not None
        assert result.name == "test-agent"
        assert result.id == agent.id
        assert result.tools == ["tool-1"]
        assert result.workflows == ["workflow-1"]
        assert result.user_email == "owner@example.com"
        assert result.tenant_id == "tenant-1"
        mock_repository.find_by_id.assert_called_once_with(agent.id)

    def test_get_agent_by_id_not_found(self, use_case, mock_repository):
        """Test getting non-existent agent fails."""
        agent_id = "non-existent-id"
        
        # Mock repository to return None
        mock_repository.find_by_id.return_value = None

        # Execute and assert exception
        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(agent_id)
        
        assert agent_id in str(exc_info.value)

    def test_get_agent_by_id_returns_dto(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that get agent by ID returns DTO not entity."""
        agent = Agent.create(**sample_agent_data)
        mock_repository.find_by_id.return_value = agent

        # Execute use case
        result = use_case.execute(agent.id)

        # Assertions - result should be DTO, not entity
        from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
        assert isinstance(result, AgentResponseDTO)
        assert not isinstance(result, Agent)
