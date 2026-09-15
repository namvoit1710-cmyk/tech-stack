"""Submit (finalize) a pending node.

Serves ``POST /runs/{run_id}/nodes/{node_id}/submit[?task_id=...]``.

Submit is a **state transition only** — data was written to the working copy
via prior ``PATCH /data`` calls. This use case:

1. Resolves the target task (explicit ``task_id`` or the latest PENDING task
   for the node — scanning descendant sub-workflow runs so nested workflows
   can be driven from the top-level run_id).
2. Delegates to ``ResumePendingTaskUseCase`` with ``updated_input=None``. That
   does the same work the old ``POST /tasks/{id}/resume`` did:
   - Coerce values, detect HUMAN_ACTION action, mark task COMPLETED, store
     submitted data in ``run.context.variables[node_id]``, register named
     artifacts, apply HUMAN_ACTION branching, record the stage event, and
     dispatch downstream via the orchestration queue.
3. No request body — ``PATCH /data`` is the sole write path for node data.

This replaces the previous ``POST /runs/{run_id}/nodes/{node_id}/resume-input``
and ``POST /runs/{run_id}/tasks/{task_id}/resume`` routes; both are merged
into one consistent URL under the node's scope with ``?task_id=`` for loop
iterations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskCommand,
    ResumePendingTaskUseCase,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.orchestration_queue_interface import IOrchestrationQueue
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class SubmitNodeCommand:
    run_id: str = ""
    node_id: str = ""
    task_id: Optional[str] = None  # explicit loop-iteration targeting


@dataclass
class SubmitNodeResult:
    task_id: str = ""
    node_state: str = ""
    downstream_dispatched: bool = False
    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class SubmitNodeUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        stage_recorder: StageRecorder,
        orchestration_engine: Optional[IOrchestrationEngine] = None,
        data_store_service: Any = None,
        orchestration_queue: Optional[IOrchestrationQueue] = None,
        file_service_client: Any = None,
        resume_pending_task_usecase: Optional[ResumePendingTaskUseCase] = None,
        **kwargs: Any,
    ) -> None:
        self._logger = logger
        self._run_repo = run_repo
        self._task_repo = task_repo
        if resume_pending_task_usecase is not None:
            self._delegate = resume_pending_task_usecase
        else:
            self._delegate = ResumePendingTaskUseCase(
                logger=logger,
                run_repo=run_repo,
                task_repo=task_repo,
                workflow_repo=workflow_repo,
                stage_recorder=stage_recorder,
                orchestration_engine=orchestration_engine,
                data_store_service=data_store_service,
                orchestration_queue=orchestration_queue,
                file_service_client=file_service_client,
            )

    # ------------------------------------------------------------------

    def _collect_all_run_ids(self, run_id: str) -> list[str]:
        """Collect ``run_id`` plus every descendant sub-workflow run_id."""
        run_ids: list[str] = [run_id]
        run: WorkflowRun | None = self._run_repo.get_by_id(run_id)
        if not run:
            return run_ids
        queue = list(getattr(run, "child_run_ids", None) or [])
        while queue:
            child_id = queue.pop(0)
            if child_id in run_ids:
                continue
            run_ids.append(child_id)
            child = self._run_repo.get_by_id(child_id)
            if child is not None:
                queue.extend(getattr(child, "child_run_ids", None) or [])
        return run_ids

    @staticmethod
    def _is_user_cancelled(task: Any) -> bool:
        """``True`` iff this task was cancelled via the PATCH /data
        ``{_action: reject|cancel}`` short-circuit in EditNodeDataUseCase.

        Identified by ``status == FAILED`` + ``error == "user_cancelled"``
        — the marker the cancel branch writes at
        edit_node_data_use_case.py ``_cancel_pending_task``.
        """
        return (
            getattr(task, "status", None) == TaskStatus.FAILED
            and getattr(task, "error", "") == "user_cancelled"
        )

    @staticmethod
    def _idempotent_cancel_result(task: Any) -> SubmitNodeResult:
        """Build the 200 OK response for the FE's PATCH-then-SUBMIT cancel
        flow when the SUBMIT lands after the cancel already finalized the
        task. Surfaces the cancelled status so the FE renders the right
        terminal UI; ``downstream_dispatched=False`` because the cancel
        path already drove failure-edge routing via
        ``orchestrate_fail_locked``."""
        status = getattr(task, "status", None)
        return SubmitNodeResult(
            task_id=getattr(task, "id", ""),
            node_state=(
                status.value if hasattr(status, "value") else str(status)
            ),
            downstream_dispatched=False,
        )

    def _resolve_task(
        self,
        *,
        run_id: str,
        node_id: str,
        explicit_task_id: Optional[str],
    ) -> tuple[Any | None, Optional[SubmitNodeResult]]:
        """Return ``(task, None)`` on success, ``(task, idempotent_result)``
        when the task is already user-cancelled (caller short-circuits
        with a 200), or ``(None, error_result)`` on failure."""
        # Explicit task_id: honour it directly; must belong to node + be PENDING.
        if explicit_task_id:
            task = self._task_repo.get_by_id(explicit_task_id)
            if task is None or getattr(task, "node_id", "") != node_id:
                return None, SubmitNodeResult(
                    error=ErrorCode.TASK_NOT_FOUND.default_message,
                    error_code=ErrorCode.TASK_NOT_FOUND.code,
                    error_details={"task_id": explicit_task_id, "node_id": node_id},
                )
            # FE's cancel button does PATCH /data {_action: reject} then
            # POST /submit. The PATCH already finalized the task as
            # FAILED + drove ``orchestrate_fail_locked``; treat the
            # follow-up /submit as a 200 no-op instead of 409'ing on the
            # PENDING guard below.
            if self._is_user_cancelled(task):
                return task, self._idempotent_cancel_result(task)
            if task.status != TaskStatus.PENDING:
                return None, SubmitNodeResult(
                    error=ErrorCode.INVALID_TASK_STATUS.default_message,
                    error_code=ErrorCode.INVALID_TASK_STATUS.code,
                    error_details={
                        "task_id": explicit_task_id,
                        "current_status": task.status.value if hasattr(task.status, "value") else str(task.status),
                        "expected_status": "PENDING",
                    },
                )
            return task, None

        # Otherwise: scan run + descendants for a PENDING task on this node.
        run_ids = self._collect_all_run_ids(run_id)
        pending_tasks: list[Any] = []
        all_node_tasks: list[Any] = []
        for rid in run_ids:
            for t in self._task_repo.list_by_run(rid):
                if getattr(t, "node_id", "") != node_id:
                    continue
                all_node_tasks.append(t)
                if t.status == TaskStatus.PENDING:
                    pending_tasks.append(t)

        if pending_tasks:
            # Most recent (handles loop iterations).
            return max(pending_tasks, key=lambda t: getattr(t, "created_at", "") or ""), None

        if not all_node_tasks:
            return None, SubmitNodeResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"node_id": node_id, "run_id": run_id},
            )
        # No PENDING task — but if the most recent task on this node was
        # user-cancelled, surface the same idempotent 200 as the explicit
        # task_id path so the FE's "submit after cancel" works whether or
        # not it passes ``?task_id=``.
        latest = max(
            all_node_tasks,
            key=lambda t: getattr(t, "created_at", "") or "",
        )
        if self._is_user_cancelled(latest):
            return latest, self._idempotent_cancel_result(latest)
        return None, SubmitNodeResult(
            error=ErrorCode.INVALID_TASK_STATUS.default_message,
            error_code=ErrorCode.INVALID_TASK_STATUS.code,
            error_details={
                "node_id": node_id, "run_id": run_id, "expected_status": "PENDING",
            },
        )

    # ------------------------------------------------------------------

    async def execute(self, command: SubmitNodeCommand) -> SubmitNodeResult:
        self._logger.info(
            "Submitting node",
            run_id=command.run_id,
            node_id=command.node_id,
            task_id=command.task_id,
        )

        task, short_circuit = self._resolve_task(
            run_id=command.run_id,
            node_id=command.node_id,
            explicit_task_id=command.task_id,
        )
        # ``short_circuit`` carries either an error result (no task
        # resolved) or the idempotent-success result for the FE's
        # PATCH-then-SUBMIT cancel flow when the task is already
        # user-cancelled. Both bypass the delegate — the controller's
        # ``_check_error`` returns 200 when ``error`` is empty and the
        # mapped HTTP code otherwise.
        if short_circuit is not None:
            return short_circuit

        # Delegate: no ``updated_input`` — data was written by PATCH /data
        # into ``run.context.variables[node_id]``. The delegate's merge-step
        # is a no-op for us because ``updated_input`` is None.
        delegate_result = await self._delegate.execute(
            ResumePendingTaskCommand(
                run_id=task.run_id,
                task_id=task.id,
                updated_input=None,
            )
        )

        return SubmitNodeResult(
            task_id=delegate_result.task_id,
            node_state=delegate_result.status,
            downstream_dispatched=bool(delegate_result.resumed),
            error=delegate_result.error,
            error_code=delegate_result.error_code,
            error_details=delegate_result.error_details,
        )
