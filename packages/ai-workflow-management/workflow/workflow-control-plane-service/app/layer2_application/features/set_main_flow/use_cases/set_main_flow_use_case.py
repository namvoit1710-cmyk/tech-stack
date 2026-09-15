"""Set or unset the main_flow flag on a workflow."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode


@dataclass
class SetMainFlowCommand:
    workflow_id: str = ""
    main_flow: bool = False


@dataclass
class SetMainFlowResult:
    id: str = ""
    name: str = ""
    main_flow: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class SetMainFlowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: SetMainFlowCommand) -> SetMainFlowResult:
        self.logger.info("Setting main_flow", id=command.workflow_id, main_flow=command.main_flow)
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return SetMainFlowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        workflow.main_flow = command.main_flow
        workflow.updated_at = datetime.now(timezone.utc).isoformat()
        await self.workflow_repo.asave(workflow)

        return SetMainFlowResult(
            id=workflow.id,
            name=workflow.name,
            main_flow=workflow.main_flow,
        )
