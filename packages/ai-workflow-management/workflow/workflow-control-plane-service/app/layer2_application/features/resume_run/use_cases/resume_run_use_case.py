"""Resume a workflow run by ``run_id`` alone.

Single entry point for external systems that hold only the ``run_id`` and need
to continue a run without knowing which tasks are waiting. Handles two
orthogonal situations that can co-occur on the same call:

1. Run is ``RunStatus.PAUSED`` — flip to RUNNING and re-dispatch any READY
   tasks that were stalled by the pause.
2. Run has tasks in ``TaskStatus.PENDING`` (waiting at an INPUT / HUMAN_ACTION
   / DATA_EDIT / FILE_UPLOAD node) — delegate each to ``ResumePendingTaskUseCase``
   with the supplied ``updated_input``. Walks descendant sub-workflow runs so
   waiting nodes nested inside child runs also resume.

Returns an error only when neither situation applies (run is in a terminal
state, or RUNNING with no waiters).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    ResumeRunAction,
    ExecuteNodeAction,
)
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskUseCase,
    ResumePendingTaskCommand,
)
from app.layer2_application.errors import ErrorCode
from typing import Any


_logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
})


@dataclass
class ResumeRunCommand:
    run_id: str = ""
    updated_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResumeRunResult:
    resumed: bool = False
    unpaused: bool = False
    resumed_task_ids: list[str] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ResumeRunUseCase:
    def __init__(self, logger: ILogger, run_repo: IRunRepository,
                 task_repo: ITaskRepository,
                 workflow_repo: IWorkflowRepository,
                 stage_recorder: StageRecorder,
                 orchestration_engine: IOrchestrationEngine,
                 orchestration_queue: Optional[IOrchestrationQueue] = None,
                 run_context_view_store: Optional[IRunContextViewStore] = None,
                 outbox_awaiter: Any = None,
                 outbox_repo: Any = None,
                 event_log_repo: Any = None,
                 resume_pending_task_usecase: Any = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._stage_recorder = stage_recorder
        self._engine = orchestration_engine
        self._view_store = run_context_view_store
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)
        # Phase 3 Sub 3.4 — outbox-routed HITL resume.
        self._outbox_awaiter = outbox_awaiter
        self._outbox_repo = outbox_repo
        self._outbox_event_log = event_log_repo
        self._resume_pending_task_usecase = resume_pending_task_usecase

    @property
    def _outbox_enabled(self) -> bool:
        return (
            self._outbox_awaiter is not None
            and self._outbox_event_log is not None
        )

    async def execute(self, command: ResumeRunCommand) -> ResumeRunResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return ResumeRunResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        current_status = read_authoritative_status(run, self._view_store)
        if current_status in _TERMINAL_STATUSES:
            return ResumeRunResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={
                    "run_id": command.run_id,
                    "current_status": current_status,
                    "reason": f"run is in terminal status {current_status}",
                },
            )

        # 1. PAUSED → flip back to RUNNING and re-arm stalled dispatch.
        unpaused = False
        if current_status == RunStatus.PAUSED.value:
            await self._unpause(run, command)
            await self._redispatch_ready_tasks(run)
            unpaused = True

        # 2. Fan out to PENDING tasks waiting at interactive nodes —
        #    across this run and any nested sub-workflow runs.
        resumed_task_ids = await self._resume_pending_tasks(run, command)

        await self._engine.drain_events()

        # Error only when neither situation applied: the run is inert —
        # RUNNING/PENDING with no waiters, or every delegate rejected.
        if not (unpaused or resumed_task_ids):
            return ResumeRunResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={
                    "run_id": command.run_id,
                    "current_status": current_status,
                },
            )

        return ResumeRunResult(
            resumed=True,
            unpaused=unpaused,
            resumed_task_ids=resumed_task_ids,
        )

    async def _unpause(self, run: WorkflowRun, command: ResumeRunCommand) -> None:
        """Flip a PAUSED run back to RUNNING.

        Phase 3 Sub 3.4: route the audit row + ``ResumeRunAction`` through the
        outbox so replay is idempotent under HITL_RESUME=on; fall back to
        direct calls when the outbox deps are not wired.
        """
        stage_kwargs = dict(
            run_id=command.run_id,
            event_type="run.resumed",
            from_status=RunStatus.PAUSED.value,
            to_status=RunStatus.RUNNING.value,
        )
        if not (
            self._outbox_enabled
            and self._emit_stage_record_outbox(
                run_id=command.run_id, op_index=0, stage_kwargs=stage_kwargs,
            )
        ):
            self._stage_recorder.record(**stage_kwargs)

        self._engine.emit_run_status(run, "resumed")

        action_payload = dict(run_id=command.run_id)
        if not (
            self._outbox_enabled
            and self._emit_queue_enqueue_outbox(
                run_id=command.run_id, op_index=1,
                action_type="ResumeRunAction", action_payload=action_payload,
            )
        ):
            await self._queue.enqueue(ResumeRunAction(**action_payload))

        run.status = RunStatus.RUNNING

        def _apply_running(r: WorkflowRun) -> None:
            r.status = RunStatus.RUNNING

        await save_run_with_cas_retry(self.run_repo, run.id, _apply_running)

    async def _redispatch_ready_tasks(self, run: WorkflowRun) -> None:
        """Re-enqueue READY tasks that were stalled while the run was paused.

        Goes through the orchestration queue (``ExecuteNodeAction``) — the
        same fan-out path the engine uses for every dispatch — rather than
        the retired recursive walker.
        """
        if self._queue is None:
            return
        for task in await self.task_repo.alist_by_run(run.id):
            if task.status != TaskStatus.READY:
                continue
            await self._queue.enqueue(
                ExecuteNodeAction(run_id=run.id, task_id=task.id),
            )

    async def _resume_pending_tasks(
        self, run: WorkflowRun, command: ResumeRunCommand,
    ) -> list[str]:
        """Delegate every PENDING task on the run — and its descendant child
        runs — to ``ResumePendingTaskUseCase``. Returns the ids of the tasks
        that were successfully resumed.
        """
        if self._resume_pending_task_usecase is None:
            return []
        resumed_task_ids: list[str] = []
        async for scoped_run in self._iter_run_tree(run):
            for task in await self.task_repo.alist_by_run(scoped_run.id):
                if task.status != TaskStatus.PENDING:
                    continue
                result = await self._resume_pending_task_usecase.execute(
                    ResumePendingTaskCommand(
                        run_id=scoped_run.id,
                        task_id=task.id,
                        updated_input=dict(command.updated_input or {}),
                    )
                )
                if getattr(result, "resumed", False):
                    resumed_task_ids.append(task.id)
                else:
                    self.logger.warning(
                        "Run resume: delegate rejected pending task %s on "
                        "run %s: %s",
                        task.id, scoped_run.id,
                        getattr(result, "error", "") or "rejected",
                    )
        return resumed_task_ids

    async def _iter_run_tree(self, root: WorkflowRun) -> Any:
        """Yield ``root`` followed by every descendant sub-workflow run.

        Async generator — callers use ``async for scoped_run in
        self._iter_run_tree(root):``. The async form lets the per-child
        ``aget_by_id`` offload to the executor under
        ``ASYNC_HANA_ENABLED``.
        """
        seen: set[str] = set()
        stack: list[WorkflowRun] = [root]
        while stack:
            current = stack.pop()
            if current is None or current.id in seen:
                continue
            seen.add(current.id)
            yield current
            child_runs = child_runs_of(current.id, self._view_store, current)
            for child_run_id in child_runs.values():
                child = await self.run_repo.aget_by_id(child_run_id)
                if child is not None:
                    stack.append(child)

    def _emit_stage_record_outbox(
        self,
        *,
        run_id: str,
        op_index: int,
        stage_kwargs: dict[str, Any],
    ) -> bool:
        try:
            from app.layer1_domain.events import EventTopic
            from app.layer1_domain.value_objects.event_id import EventId
            from app.layer2_application.interfaces.event_log_repository_interface import (
                EventLogEnvelope,
            )

            outbox_id = EventId.derive_outbox(
                run_id, "resume_run", op_index, "stage_record",
            )
            if self._outbox_repo is not None and hasattr(self._outbox_repo, "mark_run"):
                self._outbox_repo.mark_run(run_id)
            envelope = EventLogEnvelope(
                event_id=EventId.derive(outbox_id, "requested"),
                run_id=run_id,
                topic=str(EventTopic.OUTBOX_REQUESTED),
                event_type="OutboxRequested",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "outbox_id": outbox_id,
                    "run_id": run_id,
                    "workflow_id": "",
                    "kind": "stage_record",
                    "payload": {"stage_kwargs": dict(stage_kwargs)},
                    "idempotency_key": outbox_id,
                },
                causation_id=run_id,
            )
            self._outbox_event_log.append(envelope)
            return True
        except Exception as exc:
            _logger.warning(
                "outbox stage_record append failed for resume_run run=%s op=%d: %s",
                run_id, op_index, exc,
            )
            return False

    def _emit_queue_enqueue_outbox(
        self,
        *,
        run_id: str,
        op_index: int,
        action_type: str,
        action_payload: dict[str, Any],
    ) -> bool:
        try:
            from app.layer1_domain.events import EventTopic
            from app.layer1_domain.value_objects.event_id import EventId
            from app.layer2_application.interfaces.event_log_repository_interface import (
                EventLogEnvelope,
            )

            outbox_id = EventId.derive_outbox(
                run_id, "resume_run", op_index, "queue_enqueue",
            )
            if self._outbox_repo is not None and hasattr(self._outbox_repo, "mark_run"):
                self._outbox_repo.mark_run(run_id)
            envelope = EventLogEnvelope(
                event_id=EventId.derive(outbox_id, "requested"),
                run_id=run_id,
                topic=str(EventTopic.OUTBOX_REQUESTED),
                event_type="OutboxRequested",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "outbox_id": outbox_id,
                    "run_id": run_id,
                    "workflow_id": "",
                    "kind": "queue_enqueue",
                    "payload": {
                        "action_type": action_type,
                        "action_payload": dict(action_payload),
                    },
                    "idempotency_key": outbox_id,
                },
                causation_id=run_id,
            )
            self._outbox_event_log.append(envelope)
            return True
        except Exception as exc:
            _logger.warning(
                "outbox queue_enqueue append failed for resume_run run=%s op=%d: %s",
                run_id, op_index, exc,
            )
            return False
