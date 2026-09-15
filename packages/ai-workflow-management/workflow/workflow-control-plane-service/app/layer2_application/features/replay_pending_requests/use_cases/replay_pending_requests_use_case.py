"""Replay the ``*.requested`` event for every PENDING interactive task on a run.

Use case: an external system reconnects to a run that's stuck waiting at an
INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD node. The original
``input.requested`` (or peer) event already fired and is gone from any
realtime stream the external system might be tailing. They hold only the
``run_id`` and need the request to fire again so their existing subscriber
code is woken up — ideally with a freshly-computed schema, in case upstream
data changed since the original request.

Mechanism: for each PENDING task on the run + descendant child runs, cancel
the old task (preserves audit trail with ``reason: "replayed"``) and re-run
the node handler from scratch via
``OrchestrationEngine.rerequest_pending_node``. The handler creates a brand
new PENDING task and emits a fresh ``*.requested`` event — same code path as
the first time the node was reached.

Side effects:
- Old PENDING task → CANCELLED with ``output_metadata.reason = "replayed"``.
- New PENDING task created (new task id, fresh idempotency key, fresh schema).
- Fresh ``*.requested`` event published.
- Run status is not changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.successor_processor import PENDING_NODE_TYPES
from app.layer2_application.errors import ErrorCode


@dataclass
class ReplayPendingRequestsCommand:
    run_id: str = ""


@dataclass
class ReplayedRequestEntry:
    run_id: str = ""
    node_id: str = ""
    node_type: str = ""
    old_task_id: str = ""
    new_task_id: str = ""


@dataclass
class ReplayPendingRequestsResult:
    replayed: bool = False
    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ReplayPendingRequestsUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        stage_recorder: StageRecorder,
        orchestration_engine: OrchestrationEngine,
        run_context_view_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._stage_recorder = stage_recorder
        self._engine = orchestration_engine
        self._view_store = run_context_view_store

    async def execute(self, command: ReplayPendingRequestsCommand) -> ReplayPendingRequestsResult:
        root = await self.run_repo.aget_by_id(command.run_id)
        if not root:
            return ReplayPendingRequestsResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        if root.status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
            return ReplayPendingRequestsResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={
                    "run_id": command.run_id,
                    "current_status": root.status.value,
                    "reason": "run is in a terminal state",
                },
            )

        entries: list[dict[str, Any]] = []
        for rid in await self._collect_all_run_ids(command.run_id):
            run = await self.run_repo.aget_by_id(rid)
            if run is None:
                continue
            workflow = self._engine.load_workflow_for_run(run)
            if workflow is None:
                continue
            topology = run.context.topology

            for task in await self.task_repo.alist_by_run(rid):
                if task.status != TaskStatus.PENDING:
                    continue
                node = workflow.get_node(task.node_id)
                if node is None or node.node_type not in PENDING_NODE_TYPES:
                    continue

                await self._cancel_old_task(task, rid)

                handler_result = await self._engine.rerequest_pending_node(
                    node, run, workflow, topology,
                )
                if handler_result is None or not handler_result.tasks_to_dispatch:
                    self.logger.warning(
                        "Replay produced no new PENDING task",
                        run_id=rid,
                        node_id=node.id,
                        old_task_id=task.id,
                    )
                    continue

                new_task = handler_result.tasks_to_dispatch[0]
                entries.append(
                    ReplayedRequestEntry(
                        run_id=rid,
                        node_id=node.id,
                        node_type=node.node_type.value,
                        old_task_id=task.id,
                        new_task_id=new_task.id,
                    ).__dict__
                )

        await self._engine.drain_events()
        return ReplayPendingRequestsResult(replayed=bool(entries), entries=entries)

    async def _cancel_old_task(self, task: Any, run_id: str) -> None:
        """Mark the old PENDING task CANCELLED with a ``replayed`` marker.

        Preserves audit trail rather than deleting — anyone holding the old
        task id sees a clean cancellation reason.
        """
        previous_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        task.output_metadata = {
            **(task.output_metadata or {}),
            "reason": "replayed",
        }
        await self.task_repo.asave(task)

        self._stage_recorder.record(
            run_id=run_id,
            node_id=task.node_id,
            task_id=task.id,
            event_type="node.cancelled",
            from_status=previous_status,
            to_status=TaskStatus.CANCELLED.value,
            metadata={"reason": "replay"},
        )

    async def _collect_all_run_ids(self, run_id: str) -> list[str]:
        """Return ``run_id`` plus every descendant sub-workflow run id (deduped DFS).

        Iterative DFS so the per-node ``await aget_by_id`` is naturally
        flat instead of forcing an async recursion.
        """
        seen: set[str] = set()
        ordered: list[str] = []
        stack: list[str] = [run_id]

        while stack:
            rid = stack.pop()
            if rid in seen:
                continue
            seen.add(rid)
            ordered.append(rid)
            run: WorkflowRun | None = await self.run_repo.aget_by_id(rid)
            if run is None:
                continue
            for child_id in child_runs_of(rid, self._view_store, run).values():
                if child_id not in seen:
                    stack.append(child_id)

        return ordered
