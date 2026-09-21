"""UC3: Remove Tool use case."""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.tool_repository_port import IToolRepository


class RemoveToolUseCase:
    """Use case for removing a tool from the registry.
    
    Business rule:
    - Tool must exist before deletion
    """

    def __init__(self, repository: IToolRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Tool repository port implementation
        """
        self.repository = repository

    def execute(self, tool_id: str) -> None:
        """Remove a tool from the registry via soft delete.
        
        Args:
            tool_id: ID of tool to remove
            
        Raises:
            NotFoundException: If tool doesn't exist
        """
        # Check if tool exists
        tool = self.repository.find_by_id(tool_id)
        if not tool:
            raise NotFoundException("Tool", entity_id=tool_id)

        # Delete tool
        self.repository.soft_delete(tool_id)
