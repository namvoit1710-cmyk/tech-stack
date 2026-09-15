"""Fail a running task."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    FailTaskAction,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.generation_guard import should_reject_callback
from app.layer2_application.interfaces.app_config_interface import IAppConfig


@dataclass
class FailTaskCommand:
    run_id: str = ""
    task_id: str = ""
    error: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)



@dataclass
class FailTaskResult:
    failed: bool = False
    retrying: bool = False
    attempt: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = False



class FailTaskUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository,
                 run_repo: IRunRepository, task_repo: ITaskRepository,
                 orchestration_engine: IOrchestrationEngine,
                 stage_recorder: StageRecorder,
                 orchestration_queue: Optional[IOrchestrationQueue] = None,
                 app_config: IAppConfig | None = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._app_config = app_config
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)

    async def execute(self, command: FailTaskCommand) -> FailTaskResult:
        return await self._fail_task(command)

    async def _fail_task(self, req: FailTaskCommand) -> FailTaskResult:
        run = await self.run_repo.aget_by_id(req.run_id)
        if not run:
            return FailTaskResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": req.run_id},
            )

        task = await self.task_repo.aget_by_id(req.task_id)
        if not task:
            return FailTaskResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"task_id": req.task_id},
            )

        if should_reject_callback(task, run, self.logger, handler="fail", app_config=self._app_config):
            return FailTaskResult(
                failed=False,
                error=ErrorCode.TASK_SUPERSEDED.default_message,
                error_code=ErrorCode.TASK_SUPERSEDED.code,
                error_details={
                    "task_id": req.task_id,
                    "task_generation": getattr(task, "run_generation", 0),
                    "run_generation": getattr(run, "generation", 0),
                },
            )

        # No terminal-status short-circuit (option 1 — re-drive on replay): a
        # redelivered ``task.failed`` re-runs the body. Idempotent via the same
        # sinks as CompleteTaskUseCase — the re-emitted ``NodeOutputProduced`` is
        # dropped by the event log's deterministic dedupe, ``NodeFailed`` folds
        # into the projector's ``completed_nodes`` frozenset, and successor
        # dispatch is edge-deduped. The terminal status is persisted FIRST
        # (below), so it stays correct under EXECUTOR_MODE=executor too.
        error_msg = req.error.get("message", str(req.error)) if isinstance(req.error, dict) else str(req.error)

        # Retry-on-fail is disabled: every failure routes straight through
        # the FAILED path so the workflow's failure-port routing (or run
        # completion) handles it deterministically. The worker-reported
        # ``retryable`` flag is intentionally ignored here.
        # Persist the terminal status FIRST (before emit/enqueue) so it stays
        # correct under EXECUTOR_MODE=executor; replay safety is via the
        # idempotent sinks (see note above), not save ordering.
        task.status = TaskStatus.FAILED
        task.error = error_msg
        task.completed_at = datetime.now(timezone.utc).isoformat()
        await self.task_repo.asave(task)

        # Stash error_code in output_metadata so downstream consumers
        # (and the projector replay path) can recover the original error
        # code from the task entity without losing it across hops.
        error_code = (
            req.error.get("code", "WORKER_ERROR")
            if isinstance(req.error, dict) else "WORKER_ERROR"
        )
        task.output_metadata = {**(task.output_metadata or {}), "error_code": error_code}
        await self.task_repo.asave(task)

        self._stage_recorder.record(
            run_id=req.run_id, node_id=task.node_id, task_id=req.task_id,
            event_type="node.failed",
            from_status=TaskStatus.RUNNING.value,
            to_status=TaskStatus.FAILED.value,
            metadata={"error": error_msg, "attempt": task.retry_count},
        )

        self._engine.emit_node_failed(
            task, run,
            error_code=error_code,
            error_msg=error_msg,
        )

        # Store detailed failure info in run context so downstream nodes
        # (connected via the failure port) can access it, and so the run
        # result contains meaningful error context.
        node_name = None
        workflow = self._engine.load_workflow_for_run(run)
        if workflow:
            node = workflow.get_node(task.node_id)
            if node:
                node_name = node.name

        failure_info: dict[str, Any] = {
            "error": error_msg,
            "status": "FAILED",
            "task_id": req.task_id,
            "node_id": task.node_id,
            "node_name": node_name or task.node_id,
            "worker_type": task.worker_type or "",
            "attempt": task.retry_count,
            "failed_at": task.completed_at,
        }
        # Include worker outputs (e.g. HTTP response body, headers,
        # status_code) so downstream nodes and the UI can inspect
        # what the external API actually returned on failure.
        if req.outputs:
            failure_info.update(req.outputs)
        # Phase 3b.2a: route the failure write-set through
        # ``apply_state_updates_as_files`` — the projector rebuilds
        # ``variables`` from the emitted ``NodeOutputProduced``.
        if self._engine and workflow is not None:
            await self._engine.apply_state_updates_as_files(
                {task.node_id: failure_info},
                run,
                workflow,
                source_node_id=task.node_id,
                causation_id=task.id,
                output_port="failure",
                attempt_id=task.idempotency_key or "",
            )

        # Enqueue orchestration for async processing (failure routing + completion check)
        await self._queue.enqueue(FailTaskAction(
            run_id=req.run_id, task_id=req.task_id,
        ))

        return FailTaskResult(failed=True, error=error_msg)


