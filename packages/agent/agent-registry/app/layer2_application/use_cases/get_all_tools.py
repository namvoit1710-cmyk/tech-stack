"""UC-GetAllTools: Get All Tools use case."""

from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.interfaces.tool_repository_port import IToolRepository


class GetAllToolsUseCase:
    """Use case for retrieving all tools from the registry.
    
    This is a simple read operation that returns all tools
    without any filtering or complex logic.
    """

    def __init__(self, repository: IToolRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Tool repository port implementation
        """
        self.repository = repository
    
    def execute(self) -> list[ToolResponseDTO]:
        """Get all tools from the registry.
        
        Returns:
            List of all tools as DTOs
        """
        # Fetch all tools
        tools = self.repository.find_all()

        # Convert to DTOs
        return ToolResponseDTO.from_entities(tools)
