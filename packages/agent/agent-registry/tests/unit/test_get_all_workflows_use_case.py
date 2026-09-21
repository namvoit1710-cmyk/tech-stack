"""Unit tests for GetAllWorkflowsUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.workflow import Workflow
from app.layer2_application.use_cases.get_all_workflows import GetAllWorkflowsUseCase


class TestGetAllWorkflowsUseCase:
    """Test GetAllWorkflowsUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock workflow repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetAllWorkflowsUseCase(repository=mock_repository)

    def test_get_all_workflows_empty(self, use_case, mock_repository):
        """Test getting all workflows when none exist."""
        mock_repository.find_all.return_value = []

        result = use_case.execute()

        assert result == []
        mock_repository.find_all.assert_called_once()

    def test_get_all_workflows_with_data(
        self, use_case, mock_repository, sample_workflow_data
    ):
        """Test getting all workflows when multiple exist."""
        workflow1 = Workflow(**sample_workflow_data)
        workflow2_data = sample_workflow_data.copy()
        workflow2_data["name"] = "workflow-2"
        workflow2 = Workflow(**workflow2_data)

        mock_repository.find_all.return_value = [workflow1, workflow2]

        result = use_case.execute()

        assert len(result) == 2
        assert result[0].name == "test-workflow"
        assert result[1].name == "workflow-2"
        mock_repository.find_all.assert_called_once()

    def test_get_all_workflows_returns_dtos(
        self, use_case, mock_repository, sample_workflow_data
    ):
        """Test that get all workflows returns DTOs not entities."""
        workflow = Workflow(**sample_workflow_data)
        mock_repository.find_all.return_value = [workflow]

        result = use_case.execute()

        assert len(result) == 1
        from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
        assert isinstance(result[0], WorkflowResponseDTO)
