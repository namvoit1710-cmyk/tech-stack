"""UC: GetToolById use case."""

from app.layer2_application.interfaces.tool_repository_port import IToolRepository
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer1_domain.exceptions import NotFoundException

class GetToolByIdUseCase:
    """Use case for retrieving a tool by its ID."""
    
    def __init__(self, repository: IToolRepository):
        self.repository = repository
    
    def execute(self, tool_id: str) -> ToolResponseDTO:
        """Execute the use case to get a tool by ID.
        
        Args:
            tool_id (str): The ID of the tool to retrieve.
        
        Returns:
            ToolResponseDTO: The DTO representing the tool.
        
        Raises:
            NotFoundException: If the tool with the given ID is not found.
        """
        tool = self.repository.find_by_id(tool_id)
        if not tool:
            raise NotFoundException("Tool", entity_id=tool_id)
        return ToolResponseDTO.from_entity(tool)