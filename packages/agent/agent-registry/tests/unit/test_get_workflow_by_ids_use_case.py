"""Unit tests for GetWorkflowsByIdsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.use_cases.get_workflow_by_ids import GetWorkflowsByIdsUseCase


class TestGetWorkflowsByIdsUseCase:
    """Test GetWorkflowsByIdsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock workflow repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetWorkflowsByIdsUseCase(repository=mock_repository)

    def test_get_workflows_by_ids_success(
        self, use_case, mock_repository, sample_workflow_data
    ):
        """Test getting workflows by IDs successfully."""
        workflow1 = Workflow(**sample_workflow_data)
        workflow2_data = sample_workflow_data.copy()
        workflow2_data["name"] = "workflow-2"
        workflow2 = Workflow(**workflow2_data)

        mock_repository.find_by_ids.return_value = [workflow1, workflow2]

        result = use_case.execute([workflow1.id, workflow2.id])

        assert len(result) == 2
        mock_repository.find_by_ids.assert_called_once_with([workflow1.id, workflow2.id])

    def test_get_workflows_by_ids_not_found(self, use_case, mock_repository):
        """Test getting workflows by IDs when some don't exist."""
        mock_repository.find_by_ids.side_effect = NotFoundException("Workflow", entity_id="missing-id")

        with pytest.raises(NotFoundException):
            use_case.execute(["missing-id"])

    def test_get_workflows_by_ids_empty_list(self, use_case, mock_repository):
        """Test getting workflows with empty ID list."""
        mock_repository.find_by_ids.return_value = []

        result = use_case.execute([])

        assert result == []
        mock_repository.find_by_ids.assert_called_once_with([])
