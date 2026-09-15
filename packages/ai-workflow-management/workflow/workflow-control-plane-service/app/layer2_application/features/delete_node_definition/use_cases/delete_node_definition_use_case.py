"""Delete a node definition."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode


@dataclass
class DeleteNodeDefinitionCommand:
    node_definition_id: str = ""


@dataclass
class DeleteNodeDefinitionResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteNodeDefinitionUseCase:
    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo

    async def execute(self, command: DeleteNodeDefinitionCommand) -> DeleteNodeDefinitionResult:
        self.logger.info("Deleting node definition", id=command.node_definition_id)
        nd = self.node_definition_repo.get_by_id(command.node_definition_id)
        if not nd:
            return DeleteNodeDefinitionResult(
                error=ErrorCode.NODE_DEFINITION_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_DEFINITION_NOT_FOUND.code,
                error_details={"node_definition_id": command.node_definition_id},
            )

        # Check if any other definitions reference this as parent
        children = [
            d for d in self.node_definition_repo.list_by_tenant(nd.tenant_id)
            if d.parent_definition_id == nd.id
        ]
        if children:
            child_name_list = [c.name for c in children]
            return DeleteNodeDefinitionResult(
                error=ErrorCode.CANNOT_DELETE_IN_USE.default_message,
                error_code=ErrorCode.CANNOT_DELETE_IN_USE.code,
                error_details={"children": child_name_list},
            )

        self.node_definition_repo.delete(command.node_definition_id)
        return DeleteNodeDefinitionResult(deleted=True)
