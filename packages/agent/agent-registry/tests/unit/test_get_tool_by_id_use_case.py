"""Unit tests for GetToolByIdUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.use_cases.get_tool_by_id import GetToolByIdUseCase


class TestGetToolByIdUseCase:
    """Test GetToolByIdUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetToolByIdUseCase(repository=mock_repository)

    def test_get_tool_by_id_success(self, use_case, mock_repository, sample_tool_data):
        """Test successfully getting a tool by ID."""
        tool = Tool(**sample_tool_data)
        mock_repository.find_by_id.return_value = tool

        result = use_case.execute(tool.id)

        assert isinstance(result, ToolResponseDTO)
        assert result.id == tool.id
        assert result.name == tool.name
        mock_repository.find_by_id.assert_called_once_with(tool.id)

    def test_get_tool_by_id_not_found(self, use_case, mock_repository):
        """Test getting a non-existent tool fails."""
        tool_id = "missing-tool"
        mock_repository.find_by_id.return_value = None

        with pytest.raises(NotFoundException, match="Tool with ID 'missing-tool' not found"):
            use_case.execute(tool_id)

        mock_repository.find_by_id.assert_called_once_with(tool_id)