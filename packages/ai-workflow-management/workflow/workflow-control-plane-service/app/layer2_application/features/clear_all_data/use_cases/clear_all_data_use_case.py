"""Clear all workflow, run, and node definition data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.stage_history_repository_interface import IStageHistoryRepository
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository


@dataclass
class ClearAllDataCommand:
    confirm: bool = False


@dataclass
class ClearAllDataResult:
    cleared: bool = False
    workflows_deleted: int = 0
    runs_deleted: int = 0
    tasks_deleted: int = 0
    stages_deleted: int = 0
    node_definitions_deleted: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ClearAllDataUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        stage_history_repo: IStageHistoryRepository,
        node_definition_repo: Optional[INodeDefinitionRepository] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.stage_history_repo = stage_history_repo
        self.node_definition_repo = node_definition_repo

    async def execute(self, command: ClearAllDataCommand) -> ClearAllDataResult:
        if not command.confirm:
            return ClearAllDataResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "confirm"},
            )

        self.logger.info("Clearing all workflow, run, and node definition data")

        # Delete in dependency order: stages -> tasks -> runs -> workflows -> node definitions
        stages_deleted = self.stage_history_repo.delete_all()
        tasks_deleted = self.task_repo.delete_all()
        runs_deleted = self.run_repo.delete_all()
        workflows_deleted = self.workflow_repo.delete_all()
        node_definitions_deleted = self.node_definition_repo.delete_all() if self.node_definition_repo else 0

        self.logger.info(
            "Clear all data complete",
            workflows=workflows_deleted,
            runs=runs_deleted,
            tasks=tasks_deleted,
            stages=stages_deleted,
            node_definitions=node_definitions_deleted,
        )

        return ClearAllDataResult(
            cleared=True,
            workflows_deleted=workflows_deleted,
            runs_deleted=runs_deleted,
            tasks_deleted=tasks_deleted,
            stages_deleted=stages_deleted,
            node_definitions_deleted=node_definitions_deleted,
        )
