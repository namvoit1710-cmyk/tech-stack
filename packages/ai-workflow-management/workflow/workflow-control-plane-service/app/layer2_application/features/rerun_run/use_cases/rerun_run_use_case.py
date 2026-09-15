"""Run-level rerun: reset state from an anchor node (or from entry nodes)
and re-dispatch.

Serves ``POST /runs/{run_id}/rerun`` with body
``{from_node_id?, rerun_strategy?, variables?}``.

Behaviour:

- **With ``from_node_id``**: collect every downstream node via
  ``collect_downstream_nodes_to_reset``; remove them from completed/skipped
  sets; delegate to ``RerunTaskUseCase`` for the anchor node's most-recent
  task. The engine handles successor processing naturally — downstream tasks
  are re-entered when preconditions match again.
- **Without ``from_node_id``**: rerun each entry node independently. Used
  for "restart whole run."
- **``variables``** (optional): shallow-merged into ``run.context.variables``
  *before* dispatch so the new run sees updated values.

``rerun_strategy`` is accepted for symmetry with PATCH /data's rerun field
but is currently ignored by this endpoint — run-level rerun is always a
cascade by definition. Future: could use it to gate "dry run" behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.read.node_data_projection import find_latest_task_for_node
from app.layer2_application.orchestration.successor_processor import PENDING_NODE_TYPES
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.rerun_task.use_cases.rerun_task_use_case import (
    RerunTaskCommand,
    RerunTaskUseCase,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class RerunRunCommand:
    run_id: str = ""
    from_node_id: Optional[str] = None
    rerun_strategy: Optional[str] = "cascade"   # reserved; not yet acted on
    variables: Optional[dict[str, Any]] = None


@dataclass
class RerunRunResult:
    reset_node_ids: list[str] = field(default_factory=list)
    dispatched_task_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class RerunRunUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        orchestration_engine: Optional[IOrchestrationEngine] = None,
        rerun_task: Optional[RerunTaskUseCase] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._engine = orchestration_engine
        self._rerun_task = rerun_task

    async def execute(self, command: RerunRunCommand) -> RerunRunResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return self._error(ErrorCode.RUN_NOT_FOUND, {"run_id": command.run_id})

        workflow = self._engine.load_workflow_for_run(run)
        if not workflow:
            return self._error(
                ErrorCode.WORKFLOW_NOT_FOUND, {"workflow_id": run.workflow_id},
            )

        topology = getattr(getattr(run, "context", None), "topology", None)
        if topology is None:
            return self._error(
                ErrorCode.INTERNAL_ERROR,
                {"message": "run has no topology — cannot compute reset set"},
            )

        # 1. Apply variable overrides (pre-dispatch).
        if command.variables:
            ctx_vars = getattr(run.context, "variables", None)
            if ctx_vars is None:
                run.context.variables = dict(command.variables)
            else:
                ctx_vars.update(command.variables)

        # 2. Determine reset set.
        if command.from_node_id:
            if self._get_node(workflow, command.from_node_id) is None:
                return self._error(
                    ErrorCode.NODE_NOT_FOUND,
                    {"run_id": command.run_id, "node_id": command.from_node_id},
                )
            reset_ids = topology.downstream_to_reset(command.from_node_id,
            )
            anchor_nodes = [command.from_node_id]
        else:
            # Rerun from every entry node.
            entries = list(getattr(topology, "entry_nodes", []) or [])
            if not entries:
                return self._error(
                    ErrorCode.INTERNAL_ERROR,
                    {"message": "run has no entry nodes"},
                )
            reset_ids: list[str] = []
            seen: set[str] = set()
            for entry in entries:
                for nid in topology.downstream_to_reset(entry):
                    if nid not in seen:
                        seen.add(nid)
                        reset_ids.append(nid)
            anchor_nodes = entries

        # 3. Clear run-level state for the reset set.
        ctx = run.context
        completed = set(getattr(ctx, "completed_nodes", None) or [])
        skipped = set(getattr(ctx, "skipped_nodes", None) or [])
        last_ports = getattr(ctx, "last_output_ports", None) or {}
        last_edges = getattr(ctx, "last_taken_edge_ports", None) or {}
        for nid in reset_ids:
            completed.discard(nid)
            skipped.discard(nid)
            # Drop stale node-context variables so re-dispatched handlers
            # don't pick up old output.
            ctx.variables.pop(nid, None)
            # Drop per-node port / edge mirrors so GET /data?side=output
            # doesn't surface the previous execution's "took success port"
            # info until the new dispatch overwrites it.
            last_ports.pop(nid, None)
            last_edges.pop(nid, None)
        ctx.completed_nodes = completed
        ctx.skipped_nodes = skipped

        # 3a. Restart-from-failure: bump generation so any in-flight callback
        # from the prior cycle is rejected as TASK_SUPERSEDED. Required by
        # the run-generation guard (see docs/design/run-generation-guard.md);
        # ``TestFullWorkflowUseCase`` and ``ResetTestRunUseCase`` already
        # do this — rerun is the third "new execution" entry point.
        run.generation = (getattr(run, "generation", 0) or 0) + 1

        # 3b. If the run is currently in a terminal status, transition back
        # to RUNNING so engine guards (and ``check_run_completion``) don't
        # treat the run as done. ``RerunTaskUseCase`` does the same flip
        # for the TASK branch (lines 192-199), but the PENDING-node branch
        # below bypasses that path.
        if run.status in (RunStatus.FAILED, RunStatus.COMPLETED):
            run.status = RunStatus.RUNNING
            run.error = None
            run.output_data = None
            run.completed_at = None
        await self.run_repo.asave(run)

        # 4. Dispatch each anchor node.
        # Pause-and-wait nodes (INPUT / DATA_EDIT / HUMAN_ACTION / FILE_UPLOAD)
        # cannot be re-driven via ``RerunTaskUseCase`` — that path sets the
        # task to RUNNING, but the engine expects these nodes to sit in
        # PENDING until the user submits. For those, mirror the replay flow:
        # cancel the prior task and call ``rerequest_pending_node`` to mint
        # a fresh PENDING task plus the corresponding ``*.requested`` event.
        dispatched_task_ids: list[str] = []
        all_tasks = await self.task_repo.alist_by_run(command.run_id)
        for anchor_node_id in anchor_nodes:
            anchor_node = self._get_node(workflow, anchor_node_id)
            latest = find_latest_task_for_node(all_tasks, anchor_node_id)

            if anchor_node is not None and anchor_node.node_type in PENDING_NODE_TYPES:
                if latest is not None:
                    await self._cancel_old_task(latest)
                handler_result = await self._engine.rerequest_pending_node(
                    anchor_node, run, workflow, topology,
                )
                if handler_result is None or not handler_result.tasks_to_dispatch:
                    self.logger.warning(
                        "rerun: rerequest_pending_node produced no task",
                        run_id=command.run_id, node_id=anchor_node_id,
                    )
                    continue
                new_task = handler_result.tasks_to_dispatch[0]
                dispatched_task_ids.append(getattr(new_task, "id", ""))
                continue

            if latest is None:
                # No prior task — the engine will pick it up naturally when
                # an upstream node dispatches it.
                continue
            if self._rerun_task is None:
                # Soft fail: state was reset but nothing dispatched.
                continue
            rr = await self._rerun_task.execute(
                RerunTaskCommand(
                    run_id=command.run_id,
                    task_id=getattr(latest, "id", ""),
                    new_input=None,
                ),
            )
            if getattr(rr, "task_id", ""):
                dispatched_task_ids.append(rr.task_id)

        return RerunRunResult(
            reset_node_ids=reset_ids,
            dispatched_task_ids=dispatched_task_ids,
        )

    async def _cancel_old_task(self, task: Any) -> None:
        """Mark the prior task CANCELLED with a ``replaced_by_rerun`` marker.

        Mirrors ``ReplayPendingRequestsUseCase._cancel_old_task`` so the
        audit trail records why the task ended.
        """
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        task.output_metadata = {
            **(task.output_metadata or {}),
            "reason": "replaced_by_rerun",
        }
        await self.task_repo.asave(task)

    # ------------------------------------------------------------------

    @staticmethod
    def _get_node(workflow: Any, node_id: str) -> Any:
        node = workflow.get_node(node_id) if hasattr(workflow, "get_node") else None
        if node is not None:
            return node
        for n in getattr(workflow, "nodes", []) or []:
            if getattr(n, "id", "") == node_id:
                return n
        return None

    @staticmethod
    def _error(code: ErrorCode, details: dict[str, Any]) -> RerunRunResult:
        return RerunRunResult(
            error=code.default_message,
            error_code=code.code,
            error_details=details,
        )
