"""Unit tests for WorkflowResponseDTO."""

import pytest
from datetime import datetime, timezone

from app.layer1_domain.entities.workflow import Workflow
from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO


class TestWorkflowResponseDTO:
    """Test WorkflowResponseDTO."""

    def test_from_entity(self, sample_workflow_data):
        """Test creating DTO from entity."""
        workflow = Workflow(**sample_workflow_data)

        dto = WorkflowResponseDTO.from_entity(workflow)

        assert dto.id == workflow.id
        assert dto.name == workflow.name
        assert dto.description == workflow.description
        assert dto.version == workflow.version
        assert dto.status == workflow.status
        assert dto.main_flow == workflow.main_flow
        assert dto.input_schema == workflow.input_schema
        assert dto.output_schema == workflow.output_schema
        assert dto.created_at == workflow.created_at
        assert dto.updated_at == workflow.updated_at

    def test_from_entities(self, sample_workflow_data):
        """Test creating DTOs from multiple entities."""
        workflow1 = Workflow(**sample_workflow_data)
        workflow2_data = sample_workflow_data.copy()
        workflow2_data["name"] = "workflow-2"
        workflow2 = Workflow(**workflow2_data)

        dtos = WorkflowResponseDTO.from_entities([workflow1, workflow2])

        assert len(dtos) == 2
        assert dtos[0].name == "test-workflow"
        assert dtos[1].name == "workflow-2"

    def test_from_entities_empty(self):
        """Test creating DTOs from empty list."""
        dtos = WorkflowResponseDTO.from_entities([])

        assert dtos == []

    def test_dto_immutability(self, sample_workflow_data):
        """Test that DTO is frozen/immutable."""
        workflow = Workflow(**sample_workflow_data)
        dto = WorkflowResponseDTO.from_entity(workflow)

        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            dto.name = "modified"
