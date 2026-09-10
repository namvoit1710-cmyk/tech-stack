"""Tool Response DTO - Output contract for tool data."""

from dataclasses import dataclass
from datetime import datetime

from app.layer1_domain.entities.tool import Tool


@dataclass(frozen=True)
class ToolResponseDTO:
    """Output DTO for tool data.
    
    Converts domain Tool entity to a data transfer object
    suitable for API responses.
    """

    id: str
    name: str
    description: str
    protocol: str
    endpoint: str | None
    parameters_schema: dict
    response_schema: dict
    auth_config: dict
    version: str
    status: str
    metadata: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, tool: Tool) -> "ToolResponseDTO":
        """Create DTO from Tool entity.
        
        Args:
            tool: Tool domain entity
            
        Returns:
            ToolResponseDTO instance
        """
        return cls(
            id=tool.id,
            name=tool.name,
            description=tool.description,
            protocol=tool.protocol.value,
            endpoint=tool.endpoint,
            parameters_schema=tool.parameters_schema,
            response_schema=tool.response_schema,
            auth_config=tool.auth_config,
            version=tool.version,
            status=tool.status.value,
            metadata=tool.metadata,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )

    @classmethod
    def from_entities(cls, tools: list[Tool]) -> list["ToolResponseDTO"]:
        """Create list of DTOs from list of Tool entities.
        
        Args:
            tools: List of Tool domain entities
            
        Returns:
            List of ToolResponseDTO instances
        """
        return [cls.from_entity(tool) for tool in tools]
