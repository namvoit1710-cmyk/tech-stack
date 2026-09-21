"""Unit tests for RegisterToolUseCase."""

from dataclasses import replace
from unittest.mock import Mock
from uuid6 import uuid7

import pytest

from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.exceptions import (
    InvalidDataException,
)
from app.layer2_application.dtos.register_tool_dto import RegisterToolDTO
from app.layer2_application.use_cases.register_tool import RegisterToolUseCase


class TestRegisterToolUseCase:
    """Test RegisterToolUseCase."""

    @pytest.fixture
    def mock_repository(self):
        """Mock tool repository."""
        return Mock()

    @pytest.fixture
    def mock_uuid_generator(self):
        """Mock UUID generator."""
        generator = Mock()
        generator.generate_uuid.side_effect = [str(uuid7()) for _ in range(20)]
        return generator

    @pytest.fixture
    def use_case(self, mock_repository, mock_uuid_generator):
        """Create use case instance."""
        return RegisterToolUseCase(
            repository=mock_repository,
            uuid_generator=mock_uuid_generator,
        )

    @pytest.fixture
    def register_tool_dto(self):
        """Create register tool DTO."""
        return RegisterToolDTO(
            name="test-tool",
            description="A test tool",
            protocol="rest",
            version="1.0.0",
            status="active",
            parameters_schema={"type": "object"},
            response_schema={"type": "object"},
            auth_config={"type": "none"},
            metadata={},
        )

    def test_register_tool_success(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test successfully registering a tool."""
        # Execute use case
        result = use_case.execute(register_tool_dto)

        # Assertions
        assert result is not None  # Returns tool ID
        mock_repository.save.assert_called_once()

    def test_register_tool_without_endpoint(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test registering a tool when endpoint is omitted."""
        result = use_case.execute(register_tool_dto)

        assert result is not None
        saved_tool = mock_repository.save.call_args.args[0]
        assert saved_tool.endpoint is None

    def test_register_tool_duplicate_name_allowed(
        self, use_case, mock_repository, register_tool_dto, sample_tool_data
    ):
        """Test that registering tool with duplicate name is allowed."""
        existing_tool = Tool(**sample_tool_data)
        mock_repository.find_by_name.return_value = existing_tool

        result = use_case.execute(register_tool_dto)

        assert result is not None
        mock_repository.save.assert_called_once()

    def test_register_tool_with_different_protocols(
        self, use_case, mock_repository
    ):
        """Test registering tools with different protocols."""
        protocols = ["rest", "grpc", "mcp", "websocket"]
        
        for protocol in protocols:
            dto = RegisterToolDTO(
                name=f"{protocol}-tool",
                description=f"Tool using {protocol}",
                protocol=protocol,
                version="1.0.0",
                status="active",
                parameters_schema={},
                response_schema={},
                auth_config={},
                metadata={},
            )
            
            # Execute use case
            result = use_case.execute(dto)
            
            # Assertions
            assert result is not None

    def test_register_tool_with_invalid_protocol(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test that registering tool with invalid protocol fails."""
        invalid_dto = replace(register_tool_dto, protocol="invalid-protocol")
        
        # Execute and assert exception
        with pytest.raises(InvalidDataException) as exc_info:
            use_case.execute(invalid_dto)
        
        assert "Invalid protocol" in str(exc_info.value)
        mock_repository.save.assert_not_called()

    def test_register_tool_with_different_statuses(
        self, use_case, mock_repository
    ):
        """Test registering tools with different statuses."""
        statuses = ["active", "inactive", "deleted"]
        
        for status in statuses:
            dto = RegisterToolDTO(
                name=f"{status}-tool",
                description=f"Tool with {status} status",
                protocol="rest",
                version="1.0.0",
                status=status,
                parameters_schema={},
                response_schema={},
                auth_config={},
                metadata={},
            )
            
            # Mock repository
            mock_repository.find_by_name.return_value = None
            
            # Execute use case
            result = use_case.execute(dto)
            
            # Assertions
            assert result is not None

    def test_register_tool_with_invalid_status(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test that registering tool with invalid status fails."""
        invalid_dto = replace(register_tool_dto, status="invalid-status")
        
        # Mock repository
        mock_repository.find_by_name.return_value = None

        # Execute and assert exception
        with pytest.raises(InvalidDataException) as exc_info:
            use_case.execute(invalid_dto)
        
        assert "Invalid status" in str(exc_info.value)
        mock_repository.save.assert_not_called()

    def test_register_tool_with_custom_parameters_schema(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test registering tool with custom parameters schema."""
        updated_dto = replace(register_tool_dto, parameters_schema={
            "type": "object",
            "properties": {
                "input": {"type": "string"},
                "count": {"type": "integer"}
            },
            "required": ["input"]
        })
        
        # Mock repository
        mock_repository.find_by_name.return_value = None

        # Execute use case
        result = use_case.execute(updated_dto)

        # Assertions
        assert result is not None
        mock_repository.save.assert_called_once()

    def test_register_tool_with_custom_response_schema(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test registering tool with custom response schema."""
        updated_dto = replace(register_tool_dto, response_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "count": {"type": "integer"}
            },
            "required": ["result"]
        })

        mock_repository.find_by_name.return_value = None

        result = use_case.execute(updated_dto)

        assert result is not None
        mock_repository.save.assert_called_once()

    def test_register_tool_with_auth_config(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test registering tool with auth configuration."""
        updated_dto = replace(register_tool_dto, auth_config={
            "type": "bearer",
            "token_env_var": "API_TOKEN"
        })

        mock_repository.find_by_name.return_value = None

        result = use_case.execute(updated_dto)

        assert result is not None
        mock_repository.save.assert_called_once()

    def test_register_tool_with_loopback_endpoint(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test that registering tool with loopback endpoint is allowed."""
        updated_dto = replace(register_tool_dto, endpoint="http://localhost:8000/tool")

        mock_repository.find_by_name.return_value = None

        result = use_case.execute(updated_dto)

        assert result is not None
        saved_tool = mock_repository.save.call_args.args[0]
        assert saved_tool.endpoint == "http://localhost:8000/tool"

    def test_register_tool_with_metadata(
        self, use_case, mock_repository, register_tool_dto
    ):
        """Test registering tool with metadata."""
        updated_dto = replace(register_tool_dto, metadata={
            "category": "data-processing",
            "tags": ["database", "query"],
            "owner": "team-data"
        })
        
        # Mock repository
        mock_repository.find_by_name.return_value = None

        # Execute use case
        result = use_case.execute(updated_dto)

        # Assertions
        assert result is not None
        mock_repository.save.assert_called_once()
