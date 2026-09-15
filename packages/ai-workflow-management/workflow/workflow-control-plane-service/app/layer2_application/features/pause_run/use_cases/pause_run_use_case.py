"""Pause a running workflow run."""

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.errors import ErrorCode


@dataclass
class PauseRunCommand:
    run_id: str = ""



@dataclass
class PauseRunResult:
    paused: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)



class PauseRunUseCase:
    def __init__(self, logger: ILogger, run_repo: IRunRepository,
                 stage_recorder: StageRecorder,
                 orchestration_engine: IOrchestrationEngine,
                 run_context_view_store: Optional[IRunContextViewStore] = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self._stage_recorder = stage_recorder
        self._engine = orchestration_engine
        self._view_store = run_context_view_store

    async def execute(self, command: PauseRunCommand) -> PauseRunResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return PauseRunResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        current_status = read_authoritative_status(run, self._view_store)
        if current_status != RunStatus.RUNNING.value:
            return PauseRunResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={"run_id": command.run_id, "current_status": current_status},
            )

        run.status = RunStatus.PAUSED

        def _apply_paused(r: WorkflowRun) -> None:
            r.status = RunStatus.PAUSED

        await save_run_with_cas_retry(self.run_repo, run.id, _apply_paused)

        self._stage_recorder.record(
            run_id=command.run_id,
            event_type="run.paused",
            from_status=RunStatus.RUNNING.value,
            to_status=RunStatus.PAUSED.value,
        )

        self._engine.emit_run_status(run, "paused")

        return PauseRunResult(paused=True)
