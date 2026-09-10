"""Unit tests for RemoveToolUseCase."""

from unittest.mock import Mock

import pytest

from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.remove_tool import RemoveToolUseCase


class TestRemoveToolUseCase:
    """Test RemoveToolUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return RemoveToolUseCase(repository=mock_repository)

    def test_remove_tool_success(self, use_case, mock_repository, sample_tool_data):
        """Test successfully removing a tool."""
        tool = Tool(**sample_tool_data)

        mock_repository.find_by_id.return_value = tool

        use_case.execute(tool.id)

        mock_repository.find_by_id.assert_called_once_with(tool.id)
        mock_repository.soft_delete.assert_called_once_with(tool.id)

    def test_remove_tool_not_found(self, use_case, mock_repository):
        """Test removing non-existent tool fails."""
        tool_id = "non-existent-id"
        mock_repository.find_by_id.return_value = None

        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(tool_id)

        assert tool_id in str(exc_info.value)
        mock_repository.soft_delete.assert_not_called()