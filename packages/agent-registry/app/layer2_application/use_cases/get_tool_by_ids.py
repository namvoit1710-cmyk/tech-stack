"""UC-GetToolsByIds: Get Tools by IDs use case."""

from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.interfaces.tool_repository_port import IToolRepository


class GetToolsByIdsUseCase:
    """Use case for retrieving tools by IDs from the registry.
    
    This is a simple read operation that returns tools
    based on provided IDs without any complex logic.
    """

    def __init__(self, repository: IToolRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Tool repository port implementation
        """
        self.repository = repository
    
    def execute(self, ids: list[int]) -> list[ToolResponseDTO]:
        """Get tools by IDs from the registry.
        
        Args:
            ids: List of tool IDs to retrieve
        
        Returns:
            List of tools as DTOs
        """
        # Fetch tools by IDs
        tools = self.repository.find_by_ids(ids)

        # Convert to DTOs
        return [ToolResponseDTO.from_entity(tool) for tool in tools]
