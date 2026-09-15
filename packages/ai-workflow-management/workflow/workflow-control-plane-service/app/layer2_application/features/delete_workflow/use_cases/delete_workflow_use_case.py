"""Delete a workflow."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode


@dataclass
class DeleteWorkflowCommand:
    workflow_id: str = ""



@dataclass
class DeleteWorkflowResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)



class DeleteWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: DeleteWorkflowCommand) -> DeleteWorkflowResult:
        self.logger.info("Deleting workflow", id=command.workflow_id)

        existing = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not existing:
            return DeleteWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        await self.workflow_repo.adelete(command.workflow_id)
        return DeleteWorkflowResult(deleted=True)


