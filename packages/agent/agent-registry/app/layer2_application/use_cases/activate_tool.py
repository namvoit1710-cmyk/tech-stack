"""UC-ActivateTool: Activate a tool use case."""

from app.layer1_domain.entities.tool import ToolStatus
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.tool_response_dto import ToolResponseDTO
from app.layer2_application.interfaces.tool_repository_port import IToolRepository


class ActivateToolUseCase:
    """Use case for activating a tool."""

    def __init__(self, repository: IToolRepository):
        self.repository = repository

    def execute(self, tool_id: str) -> ToolResponseDTO:
        """Activate a tool by setting its status to ACTIVE."""
        tool = self.repository.find_by_id(tool_id)
        if not tool:
            raise NotFoundException("Tool", entity_id=tool_id)

        # Update status to ACTIVE
        updated = tool.update(status=ToolStatus.ACTIVE)

        # IF no fields were updated, skip persistence and relationship updates
        if not updated:
            return ToolResponseDTO.from_entity(tool)

        if tool.status == ToolStatus.ACTIVE:
            self.repository.update(tool)

        return ToolResponseDTO.from_entity(tool)