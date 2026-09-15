"""Cancel a running workflow run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from typing import Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.errors import ErrorCode


@dataclass
class CancelRunCommand:
    run_id: str = ""



@dataclass
class CancelRunResult:
    cancelled: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)



class CancelRunUseCase:
    def __init__(self, logger: ILogger, run_repo: IRunRepository, task_repo: ITaskRepository,
                 stage_recorder: StageRecorder,
                 orchestration_engine: IOrchestrationEngine,
                 run_context_view_store: Optional[IRunContextViewStore] = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._stage_recorder = stage_recorder
        self._engine = orchestration_engine
        self._view_store = run_context_view_store

    async def execute(self, command: CancelRunCommand) -> CancelRunResult:
        return await self._cancel_run(command)

    async def _cancel_run(self, req: CancelRunCommand) -> CancelRunResult:
        run = await self.run_repo.aget_by_id(req.run_id)
        if not run:
            return CancelRunResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": req.run_id},
            )

        current_status = read_authoritative_status(run, self._view_store)
        if current_status not in (
            RunStatus.RUNNING.value,
            RunStatus.PAUSED.value,
            RunStatus.PENDING.value,
            RunStatus.SUSPENDED.value,
            # CREATED was refused until SA-2257. A run that was written but
            # never started is *more* abandoned than a PENDING one, not less,
            # and refusing to cancel it left the only escape as a direct SQL
            # UPDATE — which would move the row while the projector, the
            # authoritative reader, kept folding the old status from the event
            # log. Measured on tenant-1: 117 runs stuck in CREATED, the oldest
            # for 31 days, with no path to a terminal state at all.
            RunStatus.CREATED.value,
        ):
            return CancelRunResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={"run_id": req.run_id, "current_status": current_status},
            )

        prev_status = current_status
        cancelled_at = datetime.now(timezone.utc).isoformat()
        task_terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        run_terminal_values = {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }

        # Phase 3b.2c: ``RunCancelled`` is authoritative — the projector
        # rebuilds status from the event, and every guard in this service
        # reads it back through ``read_authoritative_status``.
        #
        # The aggregate's STATUS column is written too. Not as a second
        # source of truth — as the only index ``GET /api/v1/runs`` has.
        # That list is SQL over the runs table: it selects STATUS, filters
        # ``STATUS IN (...)`` and orders by STARTED_AT, none of which a
        # per-run event replay can serve. Phase 3b.2c dropped the write
        # here and in the child-run sweep only; completed / failed /
        # paused / resumed / suspended / pending all kept it. So a
        # cancelled run went on reading RUNNING in the list forever.
        # Measured on tenant-1 2026-08-13: 3 of the 8 rows the list called
        # RUNNING were CANCELLED, and ``?status=CANCELLED`` matched 0 rows
        # tenant-wide — every cancel ever made was invisible to the filter.
        if self._engine:
            self._engine.emit_run_status(run, "cancelled", cancelled_by="user")

        run.status = RunStatus.CANCELLED
        run.completed_at = cancelled_at

        all_tasks = await self.task_repo.alist_by_run(req.run_id)
        for task in all_tasks:
            if task.status not in task_terminal:
                if self._engine:
                    self._engine.emit_node_cancelled(
                        run, task=task, cancelled_by="user", reason="run_cancelled",
                    )
                task.status = TaskStatus.CANCELLED
                task.output_metadata = {"reason": "run_cancelled"}
                task.completed_at = cancelled_at
                await self.task_repo.asave(task)

        def _apply_cancelled(r: WorkflowRun) -> None:
            r.status = RunStatus.CANCELLED
            r.completed_at = cancelled_at

        await save_run_with_cas_retry(self.run_repo, run.id, _apply_cancelled)

        # Release run-scoped resolver caches so cancelled runs don't leak
        # memory (file-backed materialize + OData fetch results).
        # Test doubles may not implement this method — degrade gracefully.
        drop_cache = getattr(self._engine, "drop_run_resolver_cache", None) if self._engine else None
        if callable(drop_cache):
            drop_cache(req.run_id)

        # Cascade cancel to any active child runs. Read via the projector —
        # ``run.context.child_runs`` has been empty on every run created since
        # step 5b, which silently turned this cascade into a no-op and left
        # child runs RUNNING forever.
        child_runs = child_runs_of(req.run_id, self._view_store, run)
        for child_run_id in child_runs.values():
            child_run = await self.run_repo.aget_by_id(child_run_id)
            if child_run and read_authoritative_status(child_run, self._view_store) not in run_terminal_values:
                if self._engine:
                    self._engine.emit_run_status(
                        child_run, "cancelled",
                        cancelled_by="user", reason="parent_cancelled",
                    )
                    # Parent-scoped child_run.cancelled so the FE can mark
                    # the parent's WORKFLOW node cancelled too.
                    self._engine.emit_child_run_cancelled(
                        run, child_run, reason="parent_cancelled",
                    )
                # RunStatusChanged stays authoritative; the column is the
                # list's index — see the note on the parent above.
                child_run.status = RunStatus.CANCELLED
                child_run.completed_at = cancelled_at
                child_tasks = await self.task_repo.alist_by_run(child_run_id)
                for ct in child_tasks:
                    if ct.status not in task_terminal:
                        if self._engine:
                            self._engine.emit_node_cancelled(
                                child_run, task=ct,
                                cancelled_by="user",
                                reason="parent_cancelled",
                            )
                        ct.status = TaskStatus.CANCELLED
                        ct.output_metadata = {"reason": "parent_cancelled"}
                        ct.completed_at = cancelled_at
                        await self.task_repo.asave(ct)

                def _apply_child_cancelled(r: WorkflowRun) -> None:
                    r.status = RunStatus.CANCELLED
                    r.completed_at = cancelled_at

                await save_run_with_cas_retry(
                    self.run_repo, child_run.id, _apply_child_cancelled,
                )
                if callable(drop_cache):
                    drop_cache(child_run_id)

        self._stage_recorder.record(
            run_id=req.run_id,
            event_type="run.cancelled",
            from_status=prev_status,
            to_status=RunStatus.CANCELLED.value,
        )

        return CancelRunResult(cancelled=True)


