"""Tool entity - Represents tools that agents can use.

This is a pure domain entity with NO external dependencies.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.layer1_domain.exceptions import InvalidDataException


class ToolProtocol(Enum):
    """Tool protocol enumeration."""

    REST = "rest"
    GRPC = "grpc"
    MCP = "mcp"
    WEBSOCKET = "websocket"


class ToolStatus(Enum):
    """Tool status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


@dataclass
class Tool:
    """Tool entity representing a tool that agents can use.

    Domain invariants:
    - name must be 1-255 characters
    - protocol must be valid ToolProtocol
    """

    VALIDATORS = {
        "name": "validate_name",
        "protocol": "validate_protocol",
        "status": "validate_status",
    }

    id: str
    name: str
    description: str
    protocol: ToolProtocol
    parameters_schema: dict
    response_schema: dict
    auth_config: dict
    version: str
    status: ToolStatus
    endpoint: str | None
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self) -> bool:
        """Check if tool is active."""
        return self.status == ToolStatus.ACTIVE

    def is_deleted(self) -> bool:
        """Check if tool is deleted."""
        return self.status == ToolStatus.DELETED

    def validate_status(self) -> None:
        """Validate status is a valid ToolStatus.

        Raises:
            InvalidDataException: If status is not a valid ToolStatus
        """
        if self.status not in ToolStatus:
            raise InvalidDataException("Invalid tool status", field="status")
        
    def validate_protocol(self) -> None:
        """Validate protocol is a valid ToolProtocol.

        Raises:
            InvalidDataException: If protocol is not a valid ToolProtocol
        """
        if self.protocol not in ToolProtocol:
            raise InvalidDataException("Invalid tool protocol", field="protocol")
        
    def validate_name(self) -> None:
        """Validate name is not empty and within length limits.

        Raises:
            InvalidDataException: If name is empty or too long
        """
        if not self.name or not self.name.strip():
            raise InvalidDataException("Tool name cannot be empty", field="name")
        if len(self.name) > 255:
            raise InvalidDataException(
                "Tool name cannot exceed 255 characters", field="name"
            )

    def update_attribute(self, field_name: str, value) -> bool:
        """Update an attribute with normalization and validation.

        Args:
            field_name: Name of the attribute to update
            value: New value for the attribute

        Raises:
            InvalidDataException: If the field does not exist or validation fails
        """
        if not hasattr(self, field_name):
            raise InvalidDataException(f"Invalid field name: {field_name}", field=field_name)

        if field_name == "name" and value is not None:
            value = value.strip()
        elif field_name == "endpoint" and value is not None:
            value = value.strip() or None

        if value is None or getattr(self, field_name) == value:
            return False

        setattr(self, field_name, value)

        validator_method_name = self.VALIDATORS.get(field_name)
        if validator_method_name:
            validator_method = getattr(self, validator_method_name)
            validator_method()

        return True

    @classmethod
    def create(
        cls,
        id: str,
        name: str,
        description: str,
        protocol: ToolProtocol,
        endpoint: str | None = None,
        version: str = "1.0.0",
        status: ToolStatus = ToolStatus.ACTIVE,
        parameters_schema: dict | None = None,
        response_schema: dict | None = None,
        auth_config: dict | None = None,
        metadata: dict | None = None,
    ) -> "Tool":
        """Factory method to create a new Tool with business rule validation.

        Args:
            name: Tool name (1-255 chars)
            description: Tool description
            protocol: Tool protocol (REST, GRPC, MCP, etc.)
            endpoint: Tool endpoint URL (optional)
            version: Tool version (default: "1.0.0")
            status: Tool status (default: ACTIVE)
            parameters_schema: JSON schema for tool parameters (optional)
            response_schema: JSON schema for tool response (optional)
            auth_config: Authentication configuration (optional)
            metadata: Additional metadata dict (optional)

        Returns:
            New Tool instance

        Raises:
            InvalidDataException: If validation fails
        """

        # Create tool instance
        tool = cls(
            id=id,
            name=name.strip(),
            description=description,
            protocol=protocol,
            endpoint=endpoint.strip() or None if endpoint is not None else None,
            parameters_schema=parameters_schema or {},
            response_schema=response_schema or {},
            auth_config=auth_config or {},
            version=version,
            status=status,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        for field_name in tool.VALIDATORS.values():
            getattr(tool, field_name)()

        return tool

    def update(self, **kwargs) -> bool:
        """Update tool fields with re-validation.

        Args:
            kwargs: Field names and values to update

        Raises:
            InvalidDataException: If validation fails
        """
        updated = False

        # Update fields if provided
        for field_name, value in kwargs.items():
            updated = self.update_attribute(field_name, value) or updated
            
        if updated:
            self.updated_at = datetime.now(timezone.utc)

        return updated