"""Unit tests for tool API handlers."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.layer1_domain.exceptions import InvalidDataException, NotFoundException
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer3_presentation.apis.v1.tools import get_tool_by_id, get_tools_by_ids


@pytest.fixture
def tool_dto() -> ToolResponseDTO:
    """Create a sample tool DTO for presentation tests."""
    now = datetime.now(timezone.utc)
    return ToolResponseDTO(
        id="tool-1",
        name="test-tool",
        description="A test tool",
        protocol="rest",
        endpoint="https://api.example.com/tool",
        parameters_schema={"type": "object"},
        response_schema={"type": "object"},
        auth_config={"type": "none"},
        version="1.0.0",
        status="active",
        metadata={},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_get_tools_by_ids_returns_tool_list(tool_dto):
    """It returns the mapped tool responses for the requested IDs."""
    use_case = Mock()
    use_case.execute.return_value = [tool_dto]

    result = await get_tools_by_ids(ids=[tool_dto.id], use_case=use_case)

    assert result.total == 1
    assert len(result.tools) == 1
    assert result.tools[0].id == tool_dto.id
    use_case.execute.assert_called_once_with([tool_dto.id])


@pytest.mark.asyncio
async def test_get_tools_by_ids_rejects_empty_id_list():
    """It rejects empty ID lists before calling the use case."""
    use_case = Mock()

    with pytest.raises(InvalidDataException, match="At least one tool ID must be provided"):
        await get_tools_by_ids(ids=[], use_case=use_case)

    use_case.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_tool_by_id_returns_matching_tool(tool_dto):
    """It returns the DTO from the singular get-by-id use case."""
    use_case = Mock()
    use_case.execute.return_value = tool_dto

    result = await get_tool_by_id(tool_id=tool_dto.id, use_case=use_case)

    assert result.id == tool_dto.id
    assert result.name == tool_dto.name
    use_case.execute.assert_called_once_with(tool_dto.id)


@pytest.mark.asyncio
async def test_get_tool_by_id_propagates_not_found_exception():
    """It lets the domain not-found exception bubble to the global handler."""
    use_case = Mock()
    use_case.execute.side_effect = NotFoundException("Tool", entity_id="missing-tool")

    with pytest.raises(NotFoundException, match="Tool with ID 'missing-tool' not found"):
        await get_tool_by_id(tool_id="missing-tool", use_case=use_case)

    use_case.execute.assert_called_once_with("missing-tool")