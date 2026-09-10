"""Unit tests for DeactivateToolUseCase."""

from unittest.mock import Mock

import pytest

from app.layer1_domain.entities.tool import Tool, ToolStatus
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.deactivate_tool import DeactivateToolUseCase


class TestDeactivateToolUseCase:
    """Test DeactivateToolUseCase."""

    @pytest.fixture
    def mock_repository(self):
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        return DeactivateToolUseCase(repository=mock_repository)

    def test_deactivate_tool_success(self, use_case, mock_repository, sample_tool_data):
        sample_tool_data["status"] = ToolStatus.ACTIVE
        tool = Tool(**sample_tool_data)
        mock_repository.find_by_id.return_value = tool

        result = use_case.execute(tool.id)

        assert result.status == ToolStatus.INACTIVE.value
        mock_repository.find_by_id.assert_called_once_with(tool.id)
        mock_repository.update.assert_called_once_with(tool)

    def test_deactivate_tool_not_found(self, use_case, mock_repository):
        tool_id = "non-existent-id"
        mock_repository.find_by_id.return_value = None

        with pytest.raises(NotFoundException) as exc_info:
            use_case.execute(tool_id)

        assert tool_id in str(exc_info.value)
        mock_repository.update.assert_not_called()

    def test_deactivate_already_inactive_tool(self, use_case, mock_repository, sample_tool_data):
        sample_tool_data["status"] = ToolStatus.INACTIVE
        tool = Tool(**sample_tool_data)
        mock_repository.find_by_id.return_value = tool

        result = use_case.execute(tool.id)

        assert result.status == ToolStatus.INACTIVE.value
        mock_repository.update.assert_not_called()