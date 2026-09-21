"""Register Tool DTO - Input contract for registering a new tool."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisterToolDTO:
    """Input DTO for registering a new tool.
    
    This is the contract between the API layer and the use case.
    Tools can be:
    - Protocol-based: REST, gRPC, MCP, WebSocket
    - Inline: LangChain @tool decorated functions (use metadata to indicate)
    """

    name: str
    description: str
    protocol: str  # "rest", "grpc", "mcp", "websocket"
    endpoint: str | None = None  # URL for external tools, optional for non-endpoint tools
    version: str = "1.0.0"
    status: str = "active"  # "active", "inactive"
    parameters_schema: dict | None = None  # JSON schema for tool parameters
    response_schema: dict | None = None  # JSON schema for tool response
    auth_config: dict | None = None  # Authentication configuration
    metadata: dict | None = None  # Additional metadata (e.g., {"inline": true})

    @classmethod
    def from_api_request(cls, request: dict) -> "RegisterToolDTO":
        """Create DTO from API request data.
        
        Args:
            request: Dictionary with request data
            
        Returns:
            RegisterToolDTO instance
        """
        return cls(
            name=request.get("name", ""),
            description=request.get("description", ""),
            protocol=request.get("protocol", "rest"),
            endpoint=request.get("endpoint"),
            version=request.get("version", "1.0.0"),
            status=request.get("status", "active"),
            parameters_schema=request.get("input_data"),
            response_schema=request.get("output_data"),
            auth_config=request.get("auth_config"),
            metadata=request.get("metadata"),
        )
