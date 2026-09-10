"""Unit tests for GetWorkflowByIdUseCase."""

import pytest
from unittest.mock import Mock

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer2_application.use_cases.get_workflow_by_id import GetWorkflowByIdUseCase


class TestGetWorkflowByIdUseCase:
    """Test GetWorkflowByIdUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock workflow repository."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_repository):
        """Create use case instance."""
        return GetWorkflowByIdUseCase(repository=mock_repository)

    def test_get_workflow_by_id_success(self, use_case, mock_repository, sample_workflow_data):
        """Test successfully getting a workflow by ID."""
        workflow = Workflow(**sample_workflow_data)
        mock_repository.find_by_id.return_value = workflow

        result = use_case.execute(workflow.id)

        assert isinstance(result, WorkflowResponseDTO)
        assert result.id == workflow.id
        assert result.name == workflow.name
        mock_repository.find_by_id.assert_called_once_with(workflow.id)

    def test_get_workflow_by_id_not_found(self, use_case, mock_repository):
        """Test getting a non-existent workflow fails."""
        workflow_id = "missing-workflow"
        mock_repository.find_by_id.return_value = None

        with pytest.raises(NotFoundException, match="Workflow with ID 'missing-workflow' not found"):
            use_case.execute(workflow_id)

        mock_repository.find_by_id.assert_called_once_with(workflow_id)