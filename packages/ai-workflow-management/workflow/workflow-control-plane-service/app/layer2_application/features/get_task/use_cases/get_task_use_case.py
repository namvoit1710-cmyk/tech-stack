"""Get full details of a single task within a run."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.errors import ErrorCode


@dataclass
class GetTaskCommand:
    run_id: str = ""
    task_id: str = ""


@dataclass
class GetTaskResult:
    id: str = ""
    run_id: str = ""
    node_id: str = ""
    status: str = ""
    worker_type: str | None = None
    resolved_input: dict[str, Any] | None = None
    output_ref: str | None = None
    output_metadata: dict[str, Any] | None = None
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None


class GetTaskUseCase:
    def __init__(self, logger: ILogger, task_repo: ITaskRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.task_repo = task_repo

    async def execute(self, command: GetTaskCommand) -> GetTaskResult:
        task = await self.task_repo.aget_by_id(command.task_id)
        if not task:
            return GetTaskResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"task_id": command.task_id},
            )
        if task.run_id != command.run_id:
            return GetTaskResult(
                error=ErrorCode.TASK_RUN_MISMATCH.default_message,
                error_code=ErrorCode.TASK_RUN_MISMATCH.code,
                error_details={"task_id": command.task_id, "run_id": command.run_id},
            )
        return GetTaskResult(
            id=task.id,
            run_id=task.run_id,
            node_id=task.node_id,
            status=task.status.value,
            worker_type=task.worker_type,
            resolved_input=task.resolved_input,
            output_ref=task.output_ref,
            output_metadata=task.output_metadata,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            error=task.error,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )
