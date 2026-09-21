from __future__ import annotations

from typing import Any


class CSVAgentDomainError(Exception):
    """Base exception for all CSV Agent domain errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidInputError(CSVAgentDomainError):
    """Raised when input validation fails."""

    def __init__(self, field: str, reason: str, value: Any = None):
        self.field = field
        self.value = value
        message = f"Invalid input for '{field}': {reason}"
        super().__init__(message, {"field": field, "reason": reason, "value": value})


class TransformationError(CSVAgentDomainError):
    """Raised when data transformation logic fails."""

    def __init__(
        self, operation: str, reason: str, details: dict[str, Any] | None = None
    ):
        self.operation = operation
        message = f"Transformation failed ({operation}): {reason}"
        super().__init__(message, details or {"operation": operation, "reason": reason})


class FileProcessingError(CSVAgentDomainError):
    """Raised when file operations fail."""

    def __init__(self, file_id: str, operation: str, reason: str):
        self.file_id = file_id
        self.operation = operation
        message = f"File processing failed ({operation}) for {file_id}: {reason}"
        super().__init__(
            message, {"file_id": file_id, "operation": operation, "reason": reason}
        )


class MCPCommunicationError(CSVAgentDomainError):
    """Raised when MCP server communication fails."""

    def __init__(
        self, tool_name: str, reason: str, mcp_response: dict[str, Any] | None = None
    ):
        self.tool_name = tool_name
        self.mcp_response = mcp_response
        message = f"MCP call failed ({tool_name}): {reason}"
        super().__init__(
            message,
            {"tool_name": tool_name, "reason": reason, "mcp_response": mcp_response},
        )


class LLMServiceError(CSVAgentDomainError):
    """Raised when LLM service fails."""

    def __init__(self, reason: str, prompt_info: dict[str, Any] | None = None):
        message = f"LLM service error: {reason}"
        super().__init__(message, {"reason": reason, "prompt_info": prompt_info})
