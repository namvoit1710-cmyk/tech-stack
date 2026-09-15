"""Re-run a failed or completed task with optional new input."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_snapshot_repository_interface import (
    IWorkflowSnapshotRepository,
)
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.orchestration_queue_interface import ExecuteNodeAction
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import BUILTIN_NODE_TYPES
from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.errors import ErrorCode


@dataclass
class RerunTaskCommand:
    run_id: str = ""
    task_id: str = ""
    new_input: dict[str, Any] | None = None


@dataclass
class RerunTaskResult:
    rerun: bool = False
    task_id: str = ""
    run_id: str = ""
    status: str = ""
    previous_status: str = ""
    resolved_input: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    warning: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class RerunTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        orchestration_engine: IOrchestrationEngine,
        stage_recorder: StageRecorder,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        workflow_snapshot_repo: Optional[IWorkflowSnapshotRepository] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._engine = orchestration_engine
        self._stage_recorder = stage_recorder
        self._view_store = run_context_view_store
        self._snapshot_repo = workflow_snapshot_repo

    async def execute(self, command: RerunTaskCommand) -> RerunTaskResult:
        self.logger.info("Re-running task", task_id=command.task_id, run_id=command.run_id)

        # 1. Validate run exists
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return RerunTaskResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        # 2. Validate task exists and belongs to run
        task = await self.task_repo.aget_by_id(command.task_id)
        if not task:
            return RerunTaskResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"task_id": command.task_id},
            )

        if task.run_id != command.run_id:
            return RerunTaskResult(
                error=ErrorCode.TASK_RUN_MISMATCH.default_message,
                error_code=ErrorCode.TASK_RUN_MISMATCH.code,
                error_details={"task_id": command.task_id, "run_id": command.run_id},
            )

        # 3. Task must be FAILED or COMPLETED
        allowed_statuses = {TaskStatus.FAILED, TaskStatus.COMPLETED}
        if task.status not in allowed_statuses:
            return RerunTaskResult(
                error=ErrorCode.INVALID_TASK_STATUS.default_message,
                error_code=ErrorCode.INVALID_TASK_STATUS.code,
                error_details={"task_id": command.task_id, "current_status": task.status.value},
            )

        # 4. Run must not be CANCELLED or PAUSED
        blocked_status_values = {RunStatus.CANCELLED.value, RunStatus.PAUSED.value}
        current_status = read_authoritative_status(run, self._view_store)
        if current_status in blocked_status_values:
            return RerunTaskResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={"run_id": command.run_id, "current_status": current_status},
            )

        # 4b. Re-capture a fresh snapshot from the LIVE workflow so node
        # edits (e.g. URL/body) made since the run started are reflected on
        # rerun. Decision 2026-06-07 (bugs #5/#6): rerun reflects live edits
        # rather than replaying the run's pinned definition. The new
        # snapshot id is persisted with ``run`` at step 13. Skipped when no
        # snapshot repo is wired (unit tests stub ``load_workflow_for_run``
        # off the live ``workflow_repo``, so the live config is seen anyway).
        if self._snapshot_repo is not None:
            live_workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
            if live_workflow is not None:
                run.workflow_snapshot_id = capture_snapshot(
                    live_workflow, self._snapshot_repo,
                )

        # 5. Load workflow (from the freshly-captured snapshot above)
        workflow = self._engine.load_workflow_for_run(run)
        if not workflow:
            return RerunTaskResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        # 6. Reject BUILTIN nodes
        node = workflow.get_node(task.node_id)
        if node and node.node_type in BUILTIN_NODE_TYPES:
            return RerunTaskResult(
                error=ErrorCode.INVALID_TASK_STATUS.default_message,
                error_code=ErrorCode.INVALID_TASK_STATUS.code,
                error_details={"task_id": command.task_id, "node_type": node.node_type.value, "reason": "builtin_node"},
            )

        previous_status = task.status.value

        # 7. Determine which successors are relevant for the rerun.
        topology = run.context.topology
        relevant_successor_ids = set(topology.adjacency.get(task.node_id, []))

        # Check for processed successors (warning only)
        warning = ""
        processed_successors = [
            s for s in relevant_successor_ids
            if s in run.context.completed_nodes or s in run.context.skipped_nodes
        ]
        if processed_successors:
            warning = (
                f"Downstream nodes {processed_successors} have already been processed. "
                "Re-running this task will not automatically invalidate their results."
            )

        # 8. Clear node from completed_nodes and skipped_nodes
        run.context.completed_nodes.discard(task.node_id)
        run.context.skipped_nodes.discard(task.node_id)

        # Also clear downstream nodes that may need re-evaluation
        for successor_id in relevant_successor_ids:
            run.context.skipped_nodes.discard(successor_id)
            run.context.completed_nodes.discard(successor_id)

        # 9. Clear context variables for the node
        run.context.variables.pop(task.node_id, None)
        if node and node.name and node.name != task.node_id:
            run.context.variables.pop(node.name, None)

        # Clear per-node port / edge mirrors for the node and every
        # downstream successor. Without this, stale "took success port"
        # info persists on ``GET /data?side=output`` until the new
        # dispatch re-publishes ``NodeOutputProduced`` — which may never
        # happen for successors that get skipped on the rerun.
        ports = run.context.last_output_ports
        edges = run.context.last_taken_edge_ports
        ports.pop(task.node_id, None)
        edges.pop(task.node_id, None)
        for successor_id in relevant_successor_ids:
            ports.pop(successor_id, None)
            edges.pop(successor_id, None)

        # 10. Input handling. An explicit ``new_input`` is an override that
        # wins as-is (no re-resolution). Otherwise clear ``resolved_input``
        # so the engine re-resolves it from the fresh node definition when
        # it re-executes the node (step 15) — this is what makes a node
        # edit (e.g. a changed URL) take effect on rerun instead of
        # replaying the stale resolved input.
        reresolve = command.new_input is None
        if command.new_input is not None:
            task.resolved_input = command.new_input
        else:
            task.resolved_input = None

        # 11. New idempotency key
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        task.idempotency_key = f"{task.id}-rerun-{timestamp}"

        # 12. Reset task state
        task.status = TaskStatus.RUNNING
        task.error = None
        task.output_ref = None
        task.output_metadata = None
        task.completed_at = None
        task.started_at = datetime.now(timezone.utc).isoformat()
        # Re-stamp the re-dispatched task to the run's CURRENT generation,
        # mirroring how ``TaskFactory`` stamps a fresh task. ``RerunRunUseCase``
        # bumps ``run.generation`` before delegating here (so stale in-flight
        # callbacks from the prior cycle are rejected as TASK_SUPERSEDED). Without
        # this re-stamp the freshly re-dispatched task would keep its old
        # ``run_generation`` and the generation guard would reject its OWN
        # completion callback (409) — hanging the run forever. No-op for the
        # direct task-level path (generation unchanged); required for the
        # run-level path after its bump.
        task.run_generation = run.generation
        await self.task_repo.asave(task)

        # 13. If run was FAILED or COMPLETED, move back to RUNNING
        run_was_terminal = current_status in (
            RunStatus.FAILED.value, RunStatus.COMPLETED.value,
        )
        if run_was_terminal:
            run.status = RunStatus.RUNNING
            run.error = None
            run.output_data = None
            run.completed_at = None
        await self.run_repo.asave(run)

        # Flip the projector status back to RUNNING. Without this, the
        # event log's last status-changing event stays RunCompleted /
        # RunFailed and projector-backed reads (get_run_status, etc.)
        # diverge from the aggregate (which list_runs reads). RunResumed
        # uses a non-deterministic event_id so it doesn't dedupe with any
        # prior emission for the same run.
        if run_was_terminal and self._engine:
            self._engine.emit_run_status(run, "resumed")

        # 14. Record stage
        self._stage_recorder.record(
            run_id=run.id,
            node_id=task.node_id,
            task_id=task.id,
            event_type="task.rerun",
            from_status=previous_status,
            to_status=TaskStatus.RUNNING.value,
            metadata={"previous_status": previous_status},
        )

        # 15. Dispatch.
        if reresolve:
            # Re-resolve input from the fresh node definition by routing
            # through the engine's node-execution path: the handler
            # recomputes ``resolved_input`` from the current ``input_binding``
            # and the dispatched-runner applies CSV/file-storage — exactly
            # what an edit (changed URL/body) needs to take effect. This
            # replaces the ``emit_node_dispatched(task, …)`` shortcut, which
            # would replay the now-cleared/stale ``task.resolved_input``.
            queue = getattr(self._engine, "_queue", None)
            if queue is not None:
                await queue.enqueue(
                    ExecuteNodeAction(run_id=run.id, task_id=task.id),
                )
            else:
                # No queue wired (e.g. direct unit-test engine) — execute
                # inline; same re-resolution, just synchronous.
                await self._engine.orchestrate_execute_node(run.id, task.id)
        else:
            # Explicit ``new_input`` override — dispatch it directly without
            # re-resolution so the caller-supplied input is preserved.
            self._engine.emit_node_dispatched(
                task, run, node, dispatch_reason="rerun",
            )

        return RerunTaskResult(
            rerun=True,
            task_id=task.id,
            run_id=run.id,
            status=TaskStatus.RUNNING.value,
            previous_status=previous_status,
            resolved_input=task.resolved_input or {},
            idempotency_key=task.idempotency_key,
            warning=warning,
        )
