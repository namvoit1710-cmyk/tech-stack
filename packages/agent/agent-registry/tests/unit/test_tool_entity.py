"""Unit tests for Tool entity."""

import pytest
from uuid6 import uuid7

from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.exceptions import InvalidDataException


class TestToolEntity:
    """Test Tool entity business logic."""

    def test_tool_creation(self, sample_tool_data):
        """Test creating a basic tool."""
        tool = Tool(**sample_tool_data)
        assert tool.name == "test-tool"
        assert tool.protocol == ToolProtocol.REST
        assert tool.status == ToolStatus.ACTIVE
        assert tool.endpoint == "https://api.example.com/tool"

    def test_is_active(self, sample_tool_data):
        """Test is_active method."""
        tool = Tool(**sample_tool_data)
        assert tool.is_active() is True

        tool.status = ToolStatus.INACTIVE
        assert tool.is_active() is False

        tool.status = ToolStatus.DELETED
        assert tool.is_active() is False

    def test_is_deleted(self, sample_tool_data):
        """Test is_deleted method."""
        tool = Tool(**sample_tool_data)
        assert tool.is_deleted() is False

        tool.status = ToolStatus.DELETED
        assert tool.is_deleted() is True

    def test_create_tool_normalizes_whitespace_endpoint_to_none(self):
        """Test that create normalizes whitespace-only endpoints to None."""
        tool = Tool.create(
            id=str(uuid7()),
            name="whitespace-tool",
            description="Test",
            protocol=ToolProtocol.REST,
            endpoint="   ",
        )

        assert tool.endpoint is None

    def test_create_tool_with_valid_data(self):
        """Test creating tool with valid data using factory method."""
        tool_id = str(uuid7())
        tool = Tool.create(
            id=tool_id,
            name="test-tool",
            description="A test tool",
            protocol=ToolProtocol.REST,
        )
        
        assert tool.id == tool_id
        assert tool.name == "test-tool"
        assert tool.protocol == ToolProtocol.REST
        assert tool.endpoint is None
        assert tool.version == "1.0.0"  # default
        assert tool.status == ToolStatus.ACTIVE  # default

    def test_create_tool_with_optional_endpoint(self):
        """Test creating tool without endpoint explicitly provided."""
        tool = Tool.create(
            id=str(uuid7()),
            name="optional-endpoint-tool",
            description="A tool without endpoint",
            protocol=ToolProtocol.REST,
        )

        assert tool.endpoint is None

    def test_create_tool_with_custom_parameters(self):
        """Test creating tool with custom parameters."""
        parameters_schema = {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            }
        }
        response_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "string"}
            }
        }
        auth_config = {"type": "api_key", "header": "X-API-Key"}
        metadata = {"category": "data-processing"}

        tool = Tool.create(
            id=str(uuid7()),
            name="custom-tool",
            description="Custom tool",
            protocol=ToolProtocol.GRPC,
            endpoint="https://grpc.example.com/tool",
            version="2.0.0",
            status=ToolStatus.ACTIVE,
            parameters_schema=parameters_schema,
            response_schema=response_schema,
            auth_config=auth_config,
            metadata=metadata,
        )
        
        assert tool.version == "2.0.0"
        assert tool.parameters_schema == parameters_schema
        assert tool.response_schema == response_schema
        assert tool.auth_config == auth_config
        assert tool.metadata == metadata

    def test_create_tool_with_different_protocols(self):
        """Test creating tools with different protocols."""
        protocols = [
            (ToolProtocol.REST, "https://rest.example.com/tool"),
            (ToolProtocol.GRPC, "https://grpc.example.com/tool"),
            (ToolProtocol.MCP, "https://mcp.example.com/tool"),
            (ToolProtocol.WEBSOCKET, "https://websocket.example.com/tool"),
        ]
        
        for protocol, endpoint in protocols:
            tool = Tool.create(
                id=str(uuid7()),
                name=f"{protocol.value}-tool",
                description=f"Tool using {protocol.value}",
                protocol=protocol,
                endpoint=endpoint,
            )
            assert tool.protocol == protocol
            assert tool.endpoint == endpoint

    def test_create_tool_with_different_statuses(self):
        """Test creating tools with different statuses."""
        for status in [ToolStatus.ACTIVE, ToolStatus.INACTIVE, ToolStatus.DELETED]:
            tool = Tool.create(
                id=str(uuid7()),
                name=f"{status.value}-tool",
                description=f"Tool with {status.value} status",
                protocol=ToolProtocol.REST,
                endpoint="https://api.example.com/tool",
                status=status,
            )
            assert tool.status == status

    def test_create_tool_accepts_loopback_endpoint(self):
        """Test that create accepts loopback endpoints without domain validation."""
        tool = Tool.create(
            id=str(uuid7()),
            name="loopback-tool",
            description="Test",
            protocol=ToolProtocol.REST,
            endpoint="http://localhost:8000/tool",
        )

        assert tool.endpoint == "http://localhost:8000/tool"

    def test_create_tool_accepts_non_http_endpoint(self):
        """Test that create accepts non-http endpoints without domain validation."""
        tool = Tool.create(
            id=str(uuid7()),
            name="grpc-tool",
            description="Test",
            protocol=ToolProtocol.GRPC,
            endpoint="grpc://api.example.com:50051",
        )

        assert tool.endpoint == "grpc://api.example.com:50051"

    def test_tool_metadata_defaults_to_empty_dict(self):
        """Test that metadata defaults to empty dict."""
        tool = Tool.create(
            id=str(uuid7()),
            name="test-tool",
            description="Test",
            protocol=ToolProtocol.REST,
            endpoint="https://api.example.com",
        )
        assert tool.metadata == {}

    def test_tool_timestamps_are_set(self):
        """Test that created_at and updated_at are automatically set."""
        tool = Tool.create(
            id=str(uuid7()),
            name="test-tool",
            description="Test",
            protocol=ToolProtocol.REST,
            endpoint="https://api.example.com",
        )
        assert tool.created_at is not None
        assert tool.updated_at is not None
        assert tool.created_at <= tool.updated_at
