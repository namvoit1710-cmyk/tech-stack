"""Unit tests for GetAvailableAgentsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.agent import Agent, AgentStatus
from app.layer2_application.use_cases.get_available_agents import (
    GetAvailableAgentsUseCase,
)


class TestGetAvailableAgentsUseCase:
    """Test GetAvailableAgentsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock agent repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAvailableAgentsUseCase(repository=mock_repository)

    def test_get_available_agents_empty(
        self, use_case, mock_repository
    ):
        """Test getting available agents when none exist."""
        # Mock repository to return empty list
        mock_repository.find_all.return_value = []

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert result == []
        mock_repository.find_all.assert_called_once()

    def test_get_available_business_agents(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test getting available business agents."""
        # Create active, published business agent
        sample_agent_data["status"] = AgentStatus.ACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)
        agent.mark_alive()  # Mark as alive to ensure availability
        
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 1
        assert result[0].name == "test-agent"
        assert result[0].status == "active"

    def test_inactive_business_agent_not_available(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that inactive business agents are not available."""
        # Create inactive, published business agent
        sample_agent_data["status"] = AgentStatus.INACTIVE
        sample_agent_data["is_published"] = True
        agent = Agent.create(**sample_agent_data)
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 0

    def test_unpublished_agent_not_available(
        self, use_case, mock_repository, sample_agent_data
    ):
        """Test that unpublished agents are not available."""
        # Create active but unpublished business agent
        sample_agent_data["status"] = AgentStatus.ACTIVE
        sample_agent_data["is_published"] = False
        agent = Agent.create(**sample_agent_data)
        
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 0

    def test_get_available_technical_agents(
        self,
        use_case,
        mock_repository,
        sample_technical_agent_data,
    ):
        """Test getting available technical agents when they are alive."""
        # Create technical agent
        sample_technical_agent_data["is_published"] = True
        agent = Agent.create(**sample_technical_agent_data)
        agent.mark_alive()
        
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 1
        assert result[0].name == "technical-agent"

    def test_technical_agent_not_alive_not_available(
        self,
        use_case,
        mock_repository,
        sample_technical_agent_data,
    ):
        """Test that technical agent marked not alive is not available."""
        # Create technical agent
        sample_technical_agent_data["is_published"] = True
        agent = Agent.create(**sample_technical_agent_data)
        
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 0

    def test_technical_agent_without_endpoint_available_when_alive(
        self,
        use_case,
        mock_repository,
        sample_technical_agent_data,
    ):
        """Test that endpoint presence does not affect availability once alive."""
        # Create technical agent without endpoint
        sample_technical_agent_data["is_published"] = True
        agent = Agent.create(**sample_technical_agent_data)
        agent.mark_alive()
        agent.healthcheck_endpoint = None
        
        # Mock repository
        mock_repository.find_all.return_value = [agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 1
        assert result[0].name == "technical-agent"

    def test_mixed_agent_types(
        self,
        use_case,
        mock_repository,
        sample_agent_data,
        sample_technical_agent_data,
    ):
        """Test getting available agents with mixed types."""
        # Create business agent
        sample_agent_data["status"] = AgentStatus.ACTIVE
        sample_agent_data["is_published"] = True
        business_agent = Agent.create(**sample_agent_data)
        
        # Create technical agent
        sample_technical_agent_data["is_published"] = True
        technical_agent = Agent.create(**sample_technical_agent_data)
        technical_agent.mark_alive()
        business_agent.mark_alive()
        
        # Mock repository
        mock_repository.find_all.return_value = [business_agent, technical_agent]

        # Execute use case
        result = use_case.execute()

        # Assertions
        assert len(result) == 2
        names = [r.name for r in result]
        assert "test-agent" in names
        assert "technical-agent" in names
