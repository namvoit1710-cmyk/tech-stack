"""Unit tests for workflow API handlers."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.layer1_domain.exceptions import InvalidDataException, NotFoundException
from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer3_presentation.apis.v1.workflows import get_workflow_by_id, get_workflows_by_ids


@pytest.fixture
def workflow_dto() -> WorkflowResponseDTO:
    """Create a sample workflow DTO for presentation tests."""
    now = datetime.now(timezone.utc)
    return WorkflowResponseDTO(
        id="workflow-1",
        name="test-workflow",
        description="A test workflow",
        version="1.0.0",
        status="active",
        input_schema=["input"],
        output_schema=["output"],
        main_flow=False,
        metadata={},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_get_workflows_by_ids_returns_workflow_list(workflow_dto):
    """It returns the mapped workflow responses for the requested IDs."""
    use_case = Mock()
    use_case.execute.return_value = [workflow_dto]

    result = await get_workflows_by_ids(ids=[workflow_dto.id], use_case=use_case)

    assert result.total == 1
    assert len(result.workflows) == 1
    assert result.workflows[0].id == workflow_dto.id
    use_case.execute.assert_called_once_with([workflow_dto.id])


@pytest.mark.asyncio
async def test_get_workflows_by_ids_rejects_empty_id_list():
    """It rejects empty ID lists before calling the use case."""
    use_case = Mock()

    with pytest.raises(InvalidDataException, match="At least one workflow ID must be provided"):
        await get_workflows_by_ids(ids=[], use_case=use_case)

    use_case.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_workflow_by_id_returns_matching_workflow(workflow_dto):
    """It returns the DTO from the singular get-by-id use case."""
    use_case = Mock()
    use_case.execute.return_value = workflow_dto

    result = await get_workflow_by_id(workflow_id=workflow_dto.id, use_case=use_case)

    assert result.id == workflow_dto.id
    assert result.name == workflow_dto.name
    use_case.execute.assert_called_once_with(workflow_dto.id)


@pytest.mark.asyncio
async def test_get_workflow_by_id_propagates_not_found_exception():
    """It lets the domain not-found exception bubble to the global handler."""
    use_case = Mock()
    use_case.execute.side_effect = NotFoundException("Workflow", entity_id="missing-workflow")

    with pytest.raises(NotFoundException, match="Workflow with ID 'missing-workflow' not found"):
        await get_workflow_by_id(workflow_id="missing-workflow", use_case=use_case)

    use_case.execute.assert_called_once_with("missing-workflow")