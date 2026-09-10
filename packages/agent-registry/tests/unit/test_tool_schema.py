"""Unit tests for tool presentation schemas."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer3_presentation.schemas.tool_schema import (
    RegisterToolRequest,
    ToolResponse,
    ToolSummaryListResponse,
    ToolSummaryResponse,
)


class TestRegisterToolRequest:
    """Test tool request schema validation and normalization."""

    def test_register_tool_request_accepts_optional_endpoint(self):
        request = RegisterToolRequest(
            name="  inline-tool  ",
            description="  Inline tool  ",
            protocol="REST",
            endpoint="   ",
            status="ACTIVE",
            input_data={"type": "object"},
            output_data={"type": "object"},
        )

        assert request.name == "inline-tool"
        assert request.description == "Inline tool"
        assert request.protocol == "rest"
        assert request.endpoint is None
        assert request.status == "active"

    def test_register_tool_request_rejects_invalid_protocol(self):
        with pytest.raises(ValidationError) as exc_info:
            RegisterToolRequest(
                name="test-tool",
                description="Test tool",
                protocol="invalid",
            )

        assert "protocol must be one of" in str(exc_info.value)


class TestToolResponseSchemas:
    """Test tool response schema helpers."""

    def test_tool_response_from_dto_maps_input_and_output_fields(self):
        now = datetime.now(timezone.utc)
        dto = ToolResponseDTO(
            id="tool-1",
            name="test-tool",
            description="A test tool",
            protocol="rest",
            endpoint=None,
            parameters_schema={"type": "object", "properties": {"input": {"type": "string"}}},
            response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            auth_config={"type": "none"},
            version="1.0.0",
            status="active",
            metadata={"inline": True},
            created_at=now,
            updated_at=now,
        )

        response = ToolResponse.from_dto(dto)

        assert response.input_data == dto.parameters_schema
        assert response.output_data == dto.response_schema
        assert response.endpoint is None

    def test_tool_summary_list_response_contains_summary_items(self):
        now = datetime.now(timezone.utc)
        dto = ToolResponseDTO(
            id="tool-1",
            name="test-tool",
            description="A test tool",
            protocol="rest",
            endpoint="http://localhost:8000/tool",
            parameters_schema={},
            response_schema={},
            auth_config={},
            version="1.0.0",
            status="active",
            metadata={},
            created_at=now,
            updated_at=now,
        )

        response = ToolSummaryListResponse(
            tools=[ToolSummaryResponse.from_dto(dto)],
            total=1,
        )

        assert response.total == 1
        assert len(response.tools) == 1
        assert response.tools[0].name == "test-tool"
        assert not hasattr(response.tools[0], "protocol")