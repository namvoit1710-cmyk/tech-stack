"""Unit tests for domain exceptions."""

import pytest

from app.layer1_domain.exceptions import (
    AlreadyExistsException,
    NotFoundException,
    DomainException,
    EndpointRequiredException,
    InvalidDataException,
    InvalidAgentKindException,
    InvalidOperationException,
    InvalidAgentStatusException,
    InvalidAgentConfigTypeException,
)


class TestDomainExceptions:
    """Test domain exception classes."""

    def test_domain_exception_is_base(self):
        """Test that DomainException is the base exception."""
        exception = DomainException("Test error")
        assert isinstance(exception, Exception)
        assert str(exception) == "Test error"

    def test_agent_not_found_with_id(self):
        """Test NotFoundException with agent ID."""
        exception = NotFoundException("Agent", entity_id="test-id")
        assert "test-id" in str(exception)
        assert exception.entity == "Agent"
        assert exception.entity_id == "test-id"
        assert exception.entity_name is None

    def test_agent_not_found_with_name(self):
        """Test NotFoundException with agent name."""
        exception = NotFoundException("Agent", entity_name="test-agent")
        assert "test-agent" in str(exception)
        assert exception.entity_name == "test-agent"
        assert exception.entity_id is None

    def test_agent_not_found_with_no_params(self):
        """Test NotFoundException with no parameters."""
        exception = NotFoundException("Agent")
        assert "Agent not found" in str(exception)

    def test_agent_already_exists(self):
        """Test AlreadyExistsException."""
        exception = AlreadyExistsException("Agent", "duplicate-agent")
        assert "duplicate-agent" in str(exception)
        assert exception.entity == "Agent"
        assert exception.entity_name == "duplicate-agent"

    def test_invalid_agent_data_with_field(self):
        """Test InvalidDataException with field."""
        exception = InvalidDataException("Invalid temperature", field="temperature")
        assert "Invalid temperature" in str(exception)
        assert exception.field == "temperature"

    def test_invalid_agent_data_without_field(self):
        """Test InvalidDataException without field."""
        exception = InvalidDataException("Invalid data")
        assert "Invalid data" in str(exception)
        assert exception.field is None

    def test_endpoint_required(self):
        """Test EndpointRequiredException."""
        exception = EndpointRequiredException("technical-agent")
        assert "technical-agent" in str(exception)
        assert "endpoint" in str(exception).lower()
        assert exception.entity_name == "technical-agent"

    def test_invalid_agent_kind(self):
        """Test InvalidAgentKindException."""
        exception = InvalidAgentKindException("invalid-kind")
        assert "invalid-kind" in str(exception)
        assert exception.kind == "invalid-kind"

    def test_invalid_agent_status(self):
        """Test InvalidAgentStatusException."""
        exception = InvalidAgentStatusException("invalid-status")
        assert "invalid-status" in str(exception)
        assert exception.status == "invalid-status"

    def test_invalid_agent_config_type(self):
        """Test InvalidAgentConfigTypeException."""
        exception = InvalidAgentConfigTypeException("invalid-config")
        assert "invalid-config" in str(exception)
        assert exception.config_type == "invalid-config"

    def test_invalid_agent_operation(self):
        """Test InvalidOperationException."""
        exception = InvalidOperationException("Cannot perform operation")
        assert "Cannot perform operation" in str(exception)

    def test_tool_not_found(self):
        """Test NotFoundException for tool."""
        exception = NotFoundException("Tool", entity_id="tool-id-123")
        assert "tool-id-123" in str(exception)
        assert exception.entity == "Tool"
        assert exception.entity_id == "tool-id-123"

    def test_tool_already_exists(self):
        """Test AlreadyExistsException for tool."""
        exception = AlreadyExistsException("Tool", "duplicate-tool")
        assert "duplicate-tool" in str(exception)
        assert exception.entity == "Tool"
        assert exception.entity_name == "duplicate-tool"

    def test_workflow_not_found(self):
        """Test NotFoundException for workflow."""
        exception = NotFoundException("Workflow", entity_id="workflow-id-123")
        assert "workflow-id-123" in str(exception)
        assert exception.entity == "Workflow"
        assert exception.entity_id == "workflow-id-123"

    def test_all_exceptions_inherit_from_domain_exception(self):
        """Test that all custom exceptions inherit from DomainException."""
        exceptions = [
            NotFoundException("Agent"),
            AlreadyExistsException("Agent", "test"),
            InvalidDataException("test"),
            EndpointRequiredException("test"),
            InvalidAgentKindException("test"),
            InvalidAgentStatusException("test"),
            InvalidAgentConfigTypeException("test"),
            InvalidOperationException("test")
        ]
        
        for exc in exceptions:
            assert isinstance(exc, DomainException)
            assert isinstance(exc, Exception)
