"""Reset the test run for a workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.run_file_retention import (
    delete_run_files,
    retention_variables,
)
from app.layer2_application.errors import ErrorCode


@dataclass
class ResetTestRunCommand:
    workflow_id: str = ""


@dataclass
class ResetTestRunResult:
    run_id: str = ""
    workflow_id: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ResetTestRunUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        run_repo: IRunRepository,
        task_repo: ITaskRepository | None = None,
        event_log_repo: Optional[IEventLogRepository] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        file_service_client: Optional[IFileServiceClient] = None,
        loop_state_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.run_repo = run_repo
        self.task_repo = task_repo
        self._event_log_repo = event_log_repo
        self._view_store = run_context_view_store
        self._file_service_client = file_service_client
        self._loop_state_repo = loop_state_repo

    def _clear_loop_state_rows(self, run_id: str) -> None:
        """Drop the run's ``AIW_RUN_LOOP_STATE`` rows alongside the in-memory
        clear.

        The mirror in ``HanaRunRepository.save`` only fires when ``loop_state``
        is non-empty — 6,105 of 6,116 runs have none, and an unconditional
        DELETE would put 43,199 extra statements on the save path this redesign
        exists to shorten. The cost of that gate is that a CLEAR cannot
        propagate through it, and a row left behind reads as a loop that is
        still running. This is one of exactly two callers that clear it.

        Best-effort, like ``_cleanup_run_files`` above: a stale row must not
        block a reset.
        """
        if self._loop_state_repo is None:
            return
        try:
            self._loop_state_repo.delete_run(run_id)
        except Exception:
            self.logger.warning(
                "loop_state rows not cleared on reset", run_id=run_id)

    async def execute(self, command: ResetTestRunCommand) -> ResetTestRunResult:
        self.logger.info("Resetting test run", workflow_id=command.workflow_id)

        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return ResetTestRunResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        topology = GraphTopology.from_workflow(workflow)

        # Reuse existing test run — clear state only, never create new ID
        test_run = None
        if workflow.test_run_id:
            test_run = await self.run_repo.aget_by_id(workflow.test_run_id)

        if test_run:
            # G-guard: bump generation so late callbacks from the
            # previous execution are rejected by the complete/fail guard.
            test_run.generation += 1
            test_run.context.topology = topology
            test_run.context.completed_nodes.clear()
            test_run.context.skipped_nodes.clear()
            test_run.context.triggered_nodes.clear()
            # Retention (SA-1905 E): the prior execution's file-backed outputs
            # (streamed CSV main + recursive artifacts) are about to become
            # unreferenced when we wipe ``variables``. Delete them from the file
            # service first, or every test re-run leaks them. Best-effort — a
            # miss never blocks the reset.
            await self._cleanup_run_files(
                retention_variables(test_run, self._view_store),
                test_run.context.loop_state,
            )
            test_run.context.variables = {}
            test_run.context.loop_state.clear()
            self._clear_loop_state_rows(test_run.id)
            # Clear per-node port/edge mirrors, merge-quorum buffers, child
            # run map, and scratch current_node pointer. Without this, the
            # next test execution surfaces stale "took success port" info
            # on ``GET /data?side=output`` and MERGE-in-loop nodes reuse
            # quorum state from the previous execution.
            test_run.context.last_output_ports.clear()
            test_run.context.last_taken_edge_ports.clear()
            test_run.context.merge_quorum_buffers.clear()
            test_run.context.pending_revisits.clear()
            test_run.context.child_runs.clear()
            test_run.context.current_node = ""
            test_run.status = RunStatus.CREATED
            test_run.error = None
            test_run.output_data = None
            test_run.completed_at = None
            test_run.workflow_version = workflow.version
            # Parity with TestFullWorkflowUseCase — cancel stale tasks
            # from the previous execution so list_by_run doesn't return
            # orphaned RUNNING rows to downstream check_run_completion.
            if self.task_repo is not None:
                for stale_task in await self.task_repo.alist_by_run(test_run.id):
                    stale_task.status = TaskStatus.CANCELLED
                    stale_task.completed_at = datetime.now(timezone.utc).isoformat()
                    await self.task_repo.asave(stale_task)
            # Wipe the prior generation's event log + projector cache so
            # the next test execution projects from a clean slate.
            # Lifecycle events use deterministic event_ids keyed on run.id,
            # so re-using a run otherwise leaves the projector stuck on
            # the previous terminal state.
            if self._event_log_repo is not None:
                self._event_log_repo.clear(test_run.id)
            invalidate = getattr(self._view_store, "invalidate", None)
            if callable(invalidate):
                invalidate(test_run.id)
            await self.run_repo.asave(test_run)
        else:
            # First time only — create the permanent test run
            test_run = WorkflowRun(
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                status=RunStatus.CREATED,
                is_test=True,
                context=RunContext(variables={"artifacts": {}}, topology=topology),
            )
            await self.run_repo.asave(test_run)
            workflow.test_run_id = test_run.id
            await self.workflow_repo.asave(workflow)

        return ResetTestRunResult(
            run_id=test_run.id,
            workflow_id=workflow.id,
        )

    async def _cleanup_run_files(
        self,
        variables: dict[str, Any] | None,
        loop_state: dict[str, Any] | None = None,
    ) -> None:
        """Delete every file-backed output reachable in ``variables`` — and in
        ``loop_state`` — from the file service. Best-effort: a missing client is
        a no-op, and a per-file delete failure is logged but never raised —
        retention cleanup must not fail a reset. Idempotent (delete of a gone
        file is a no-op).

        ``loop_state`` is a second root because a loop's claim-checked ``items``
        and spilled results chunks are reachable through nothing else — see
        ``run_file_retention``.

        Deletes are fanned out with bounded concurrency (see
        ``run_file_retention``) and still fully awaited here, so the
        delete-before-the-reset ordering is unchanged."""
        await delete_run_files(
            self._file_service_client, variables, self.logger, "reset",
            loop_state=loop_state,
        )
