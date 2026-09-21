"""Unit tests for GetAgentsByIdsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.use_cases.get_agent_by_ids import GetAgentsByIdsUseCase


class TestGetAgentsByIdsUseCase:
    """Test GetAgentsByIdsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAgentsByIdsUseCase(repository=mock_repository)

    def test_get_agents_by_ids_success(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test successfully getting agents by IDs."""
        first_agent = Agent.create(**sample_agent_data)
        second_agent_data = sample_agent_data.copy()
        second_agent_data["name"] = "test-agent-2"
        second_agent_data["tools"] = ["tool-2"]
        second_agent_data["workflows"] = ["workflow-2"]
        second_agent = Agent.create(**second_agent_data)

        mock_repository.find_by_ids.return_value = [first_agent, second_agent]

        result = use_case.execute([first_agent.id, second_agent.id])

        assert len(result) == 2
        assert all(isinstance(agent, AgentResponseDTO) for agent in result)
        assert result[0].tools == ["tool-1"]
        assert result[0].workflows == ["workflow-1"]
        assert result[0].user_email == "owner@example.com"
        assert result[0].tenant_id == "tenant-1"
        assert result[1].tools == ["tool-2"]
        assert result[1].workflows == ["workflow-2"]
        assert result[1].user_email == "owner@example.com"
        assert result[1].tenant_id == "tenant-1"
        mock_repository.find_by_ids.assert_called_once_with([first_agent.id, second_agent.id])

    def test_get_agents_by_ids_not_found(self, use_case, mock_repository):
        """Test empty results raise not found."""
        mock_repository.find_by_ids.return_value = []

        with pytest.raises(NotFoundException):
            use_case.execute(["missing-agent"])