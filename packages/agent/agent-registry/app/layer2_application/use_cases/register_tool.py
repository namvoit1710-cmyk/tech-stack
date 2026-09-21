"""UC-RegisterTool: Register Tool use case."""

from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.exceptions import (
    InvalidDataException,
)
from app.layer2_application.dtos.register_tool_dto import RegisterToolDTO
from app.layer2_application.interfaces.tool_repository_port import IToolRepository
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.interfaces.uuid_generator_port import IUUIDGeneratorPort

class RegisterToolUseCase:
    """Use case for registering a new tool in the registry.
    
    Supports both protocol-based tools (REST, gRPC, MCP, etc.) 
    and inline LangChain @tool decorated functions.
    
    Business rules:
    - Tool name must be 1-255 characters
    - Protocol must be valid
    """

    def __init__(self, repository: IToolRepository, uuid_generator: IUUIDGeneratorPort):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Tool repository port implementation
            uuid_generator: UUID generator port implementation
        """
        self.repository = repository
        self.uuid_generator = uuid_generator

    def execute(self, dto: RegisterToolDTO) -> str:
        """Register a new tool in the registry.
        
        Args:
            dto: Tool registration data
            
        Returns:
            ID of the newly registered tool
            
        Raises:
            InvalidDataException: If validation fails
        """
        # Convert protocol string to enum
        try:
            protocol = ToolProtocol(dto.protocol.lower())
        except ValueError:
            valid_protocols = [p.value for p in ToolProtocol]
            raise InvalidDataException(
                f"Invalid protocol '{dto.protocol}'. Must be one of: {', '.join(valid_protocols)}",
                field="protocol",
            )

        # Convert status string to enum
        try:
            status = ToolStatus(dto.status.lower())
        except ValueError:
            valid_statuses = [s.value for s in ToolStatus]
            raise InvalidDataException(
                f"Invalid status '{dto.status}'. Must be one of: {', '.join(valid_statuses)}",
                field="status",
            )

        # Create tool entity using factory method (validates business rules)
        tool = Tool.create(
            id=self.uuid_generator.generate_uuid(),
            name=dto.name,
            description=dto.description,
            protocol=protocol,
            endpoint=dto.endpoint,
            version=dto.version,
            status=status,
            parameters_schema=dto.parameters_schema,
            response_schema=dto.response_schema,
            auth_config=dto.auth_config,
            metadata=dto.metadata,
        )

        # Persist to repository
        self.repository.save(tool)

        return ToolResponseDTO.from_entity(tool)
