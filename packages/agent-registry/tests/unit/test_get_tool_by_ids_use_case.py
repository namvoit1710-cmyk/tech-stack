"""Unit tests for GetToolsByIdsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.get_tool_by_ids import GetToolsByIdsUseCase


class TestGetToolsByIdsUseCase:
    """Test GetToolsByIdsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetToolsByIdsUseCase(repository=mock_repository)

    def test_get_tools_by_ids_success(
        self, use_case, mock_repository, sample_tool_data
    ):
        """Test getting tools by IDs successfully."""
        tool1 = Tool(**sample_tool_data)
        tool2_data = sample_tool_data.copy()
        tool2_data["name"] = "tool-2"
        tool2 = Tool(**tool2_data)

        mock_repository.find_by_ids.return_value = [tool1, tool2]

        result = use_case.execute([tool1.id, tool2.id])

        assert len(result) == 2
        mock_repository.find_by_ids.assert_called_once_with([tool1.id, tool2.id])

    def test_get_tools_by_ids_not_found(self, use_case, mock_repository):
        """Test getting tools by IDs when some don't exist."""
        mock_repository.find_by_ids.side_effect = NotFoundException("Tool", entity_id="missing-id")

        with pytest.raises(NotFoundException):
            use_case.execute(["missing-id"])

    def test_get_tools_by_ids_empty_list(self, use_case, mock_repository):
        """Test getting tools with empty ID list."""
        mock_repository.find_by_ids.return_value = []

        result = use_case.execute([])

        assert result == []
        mock_repository.find_by_ids.assert_called_once_with([])
