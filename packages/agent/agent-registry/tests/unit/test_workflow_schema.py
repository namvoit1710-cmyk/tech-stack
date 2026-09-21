"""Unit tests for workflow presentation schemas."""

from datetime import datetime, timezone

import pytest

from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer3_presentation.schemas.workflow_schema import (
    WorkflowResponse,
    WorkflowListResponse,
    WorkflowFetchDataResponse,
    WorkflowFetchResponse,
)


class TestWorkflowResponseSchema:
    """Test workflow response schema helpers."""

    def test_workflow_response_from_dto_maps_all_fields(self):
        now = datetime.now(timezone.utc)
        dto = WorkflowResponseDTO(
            id="workflow-1",
            name="test-workflow",
            description="A test workflow",
            version="1.0.0",
            status="active",
            input_schema=["input_field"],
            output_schema=["output_field"],
            main_flow=True,
            metadata={"source": "test"},
            created_at=now,
            updated_at=now,
        )

        response = WorkflowResponse.from_dto(dto)

        assert response.id == dto.id
        assert response.name == dto.name
        assert response.description == dto.description
        assert response.version == dto.version
        assert response.status == dto.status
        assert response.input_schema == dto.input_schema
        assert response.output_schema == dto.output_schema
        assert response.main_flow == dto.main_flow
        assert response.metadata == dto.metadata
        assert response.created_at == dto.created_at
        assert response.updated_at == dto.updated_at

    def test_workflow_list_response_contains_workflows(self):
        now = datetime.now(timezone.utc)
        dto1 = WorkflowResponseDTO(
            id="workflow-1",
            name="workflow-1",
            description="First workflow",
            version="1.0.0",
            status="active",
            input_schema=[],
            output_schema=[],
            main_flow=False,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        dto2 = WorkflowResponseDTO(
            id="workflow-2",
            name="workflow-2",
            description="Second workflow",
            version="1.0.0",
            status="active",
            input_schema=[],
            output_schema=[],
            main_flow=True,
            metadata={"main": True},
            created_at=now,
            updated_at=now,
        )

        response = WorkflowListResponse(
            workflows=[
                WorkflowResponse.from_dto(dto1),
                WorkflowResponse.from_dto(dto2),
            ],
            total=2,
        )

        assert response.total == 2
        assert len(response.workflows) == 2
        assert response.workflows[0].name == "workflow-1"
        assert response.workflows[1].name == "workflow-2"
        assert response.workflows[1].main_flow is True
        assert response.workflows[1].metadata == {"main": True}


class TestWorkflowFetchSchema:
    """Test workflow fetch schemas."""

    def test_workflow_fetch_data_response_with_defaults(self):
        response = WorkflowFetchDataResponse(
            id="workflow-1",
            name="test-workflow",
            description="Test",
            version="1.0.0",
            status="active",
        )

        assert response.id == "workflow-1"
        assert response.main_flow is False
        assert response.input_schema is None
        assert response.output_schema is None
        assert response.metadata is None

    def test_workflow_fetch_data_response_with_all_fields(self):
        response = WorkflowFetchDataResponse(
            id="workflow-1",
            name="test-workflow",
            description="Test",
            version="1.0.0",
            status="active",
            main_flow=True,
            input_schema=["input"],
            output_schema=["output"],
            metadata={"key": "value"},
        )

        assert response.main_flow is True
        assert response.input_schema == ["input"]
        assert response.output_schema == ["output"]
        assert response.metadata == {"key": "value"}

    def test_workflow_fetch_response_with_data(self):
        data = WorkflowFetchDataResponse(
            id="workflow-1",
            name="test-workflow",
            description="Test",
            version="1.0.0",
            status="active",
            main_flow=True,
        )
        response = WorkflowFetchResponse(
            status="success",
            data=data,
        )

        assert response.status == "success"
        assert response.data is not None
        assert response.data.id == "workflow-1"

    def test_workflow_fetch_response_without_data(self):
        response = WorkflowFetchResponse(
            status="not_found",
            data=None,
        )

        assert response.status == "not_found"
        assert response.data is None
