"""UC-DeactivateTool: Deactivate a tool use case."""

from app.layer1_domain.entities.tool import ToolStatus
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.interfaces.tool_repository_port import IToolRepository


class DeactivateToolUseCase:
    """Use case for deactivating a tool."""

    def __init__(self, repository: IToolRepository):
        self.repository = repository

    def execute(self, tool_id: str) -> ToolResponseDTO:
        """Deactivate a tool by setting its status to INACTIVE."""
        tool = self.repository.find_by_id(tool_id)
        if not tool:
            raise NotFoundException("Tool", entity_id=tool_id)

        updated = tool.update(status=ToolStatus.INACTIVE)

        # IF no fields were updated, skip persistence and relationship updates
        if not updated:
            return ToolResponseDTO.from_entity(tool)

        self.repository.update(tool)

        return ToolResponseDTO.from_entity(tool)