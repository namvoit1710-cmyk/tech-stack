"""Unit tests for GetAllToolsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.tool import Tool
from app.layer2_application.use_cases.get_all_tools import GetAllToolsUseCase


class TestGetAllToolsUseCase:
    """Test GetAllToolsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAllToolsUseCase(repository=mock_repository)

    def test_get_all_tools_empty(self, use_case, mock_repository):
        """Test getting all tools when none exist."""
        mock_repository.find_all.return_value = []

        result = use_case.execute()

        assert result == []
        mock_repository.find_all.assert_called_once()

    def test_get_all_tools_with_data(
        self, use_case, mock_repository, sample_tool_data
    ):
        """Test getting all tools when multiple exist."""
        tool1 = Tool(**sample_tool_data)
        tool2_data = sample_tool_data.copy()
        tool2_data["name"] = "tool-2"
        tool2 = Tool(**tool2_data)

        mock_repository.find_all.return_value = [tool1, tool2]

        result = use_case.execute()

        assert len(result) == 2
        assert result[0].name == "test-tool"
        assert result[1].name == "tool-2"
        mock_repository.find_all.assert_called_once()

    def test_get_all_tools_returns_dtos(
        self, use_case, mock_repository, sample_tool_data
    ):
        """Test that get all tools returns DTOs not entities."""
        tool = Tool(**sample_tool_data)
        mock_repository.find_all.return_value = [tool]

        result = use_case.execute()

        assert len(result) == 1
        from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
        assert isinstance(result[0], ToolResponseDTO)
