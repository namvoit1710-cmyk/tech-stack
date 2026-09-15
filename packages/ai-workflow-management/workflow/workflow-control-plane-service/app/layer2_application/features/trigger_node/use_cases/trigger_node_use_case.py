"""Trigger a specific node in a manual-mode workflow run.

Serves ``POST /runs/{run_id}/nodes/{node_id}/trigger``.

This use case allows users to manually trigger individual nodes in a
workflow run that was created with ``execution_mode: "manual"``. The
run must be in SUSPENDED status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    ExecuteNodeAction,
)
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.errors import ErrorCode


@dataclass
class TriggerNodeCommand:
    run_id: str = ""
    node_id: str = ""
    force: bool = False  # Force trigger even if node already completed/failed


@dataclass
class TriggerNodeResult:
    triggered: bool = False
    task_id: str = ""
    node_id: str = ""
    node_name: str = ""
    status: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class TriggerNodeUseCase:
    """Use case for manually triggering a node in manual execution mode."""

    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        stage_recorder: StageRecorder,
        orchestration_engine: IOrchestrationEngine,
        orchestration_queue: Optional[IOrchestrationQueue] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._stage_recorder = stage_recorder
        self._engine = orchestration_engine
        self._view_store = run_context_view_store
        self._queue = (
            orchestration_queue
            if orchestration_queue is not None
            else getattr(
                getattr(self, "_engine", None)
                or getattr(self, "_orchestration_engine", None),
                "_queue",
                None,
            )
        )

    async def execute(self, command: TriggerNodeCommand) -> TriggerNodeResult:
        return await self._trigger_node(command)

    async def _trigger_node(self, req: TriggerNodeCommand) -> TriggerNodeResult:
        self.logger.info(
            "Triggering node",
            run_id=req.run_id,
            node_id=req.node_id,
            force=req.force,
        )

        # 1. Validate run exists
        run = await self.run_repo.aget_by_id(req.run_id)
        if not run:
            return TriggerNodeResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": req.run_id},
            )

        # 2. Validate run is in manual mode
        if run.execution_mode != "manual":
            return TriggerNodeResult(
                error="Run is not in manual execution mode",
                error_code="INVALID_EXECUTION_MODE",
                error_details={
                    "run_id": req.run_id,
                    "execution_mode": run.execution_mode,
                },
            )

        # 3. Validate run is in SUSPENDED status
        current_status = read_authoritative_status(run, self._view_store)
        if current_status != RunStatus.SUSPENDED.value:
            return TriggerNodeResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={
                    "run_id": req.run_id,
                    "current_status": current_status,
                    "expected_status": RunStatus.SUSPENDED.value,
                },
            )

        # 4. Get workflow and node
        workflow = self._engine.load_workflow_for_run(run)
        if not workflow:
            return TriggerNodeResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        node = workflow.get_node(req.node_id)
        if not node:
            return TriggerNodeResult(
                error="Node not found in workflow",
                error_code="NODE_NOT_FOUND",
                error_details={
                    "workflow_id": run.workflow_id,
                    "node_id": req.node_id,
                },
            )

        # 5. Create task for the node
        task_factory = TaskFactory()
        task = task_factory.create_task(node, run)
        await self.task_repo.asave(task)

        # 6. Set run status to RUNNING temporarily
        run.status = RunStatus.RUNNING

        def _apply_running(r: WorkflowRun) -> None:
            r.status = RunStatus.RUNNING

        await save_run_with_cas_retry(self.run_repo, run.id, _apply_running)

        self._stage_recorder.record(
            run_id=run.id,
            event_type="node.triggered",
            from_status=RunStatus.SUSPENDED.value,
            to_status=RunStatus.RUNNING.value,
            metadata={"node_id": req.node_id, "task_id": task.id},
        )

        # 7. Emit node.dispatched event (manual trigger)
        self._engine.emit_node_dispatched(
            task, run, node, dispatch_reason="manual_trigger",
        )

        # 10. Enqueue ExecuteNodeAction for the node
        await self._queue.enqueue(ExecuteNodeAction(run_id=run.id, task_id=task.id))

        return TriggerNodeResult(
            triggered=True,
            task_id=task.id,
            node_id=req.node_id,
            node_name=node.name,
            status=task.status.value,
        )
