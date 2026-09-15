"""List all workflows where main_flow is True."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class ListMainFlowsCommand:
    pass


@dataclass
class ListMainFlowsResult:
    workflows: list[dict[str, Any]] = field(default_factory=list)


class ListMainFlowsUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: ListMainFlowsCommand) -> ListMainFlowsResult:
        self.logger.info("Listing main flow workflows")
        workflows = self.workflow_repo.list_main_flows()
        result = []
        for w in workflows:
            result.append({
                "id": w.id, "name": w.name, "version": str(w.version),
                "description": w.description, "status": w.status.value,
                "created_at": w.created_at, "test_run_id": w.test_run_id,
                "routing_path": w.routing_path,
                "main_flow": w.main_flow,
            })
        return ListMainFlowsResult(workflows=result)
