"""Bulk per-node status snapshot for a run.

Lightweight read-only endpoint backing the workflow-canvas live view.
Returns one entry per node defined in the workflow with status + timing +
short error + child_run_id (when the node spawned a sub-workflow).
"""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.read.node_status_aggregator import aggregate_node_status
from app.layer2_application.read.run_state import child_runs_of, skipped_nodes_of
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class ListRunNodeStatusesCommand:
    run_id: str = ""


@dataclass
class NodeStatusEntry:
    node_id: str = ""
    node_type: str = ""
    status: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    child_run_id: str | None = None


@dataclass
class ListRunNodeStatusesResult:
    run_id: str = ""
    workflow_id: str = ""
    status: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    nodes: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListRunNodeStatusesUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        run_context_view_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._view_store = run_context_view_store

    async def execute(self, command: ListRunNodeStatusesCommand) -> ListRunNodeStatusesResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return ListRunNodeStatusesResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
        if not workflow:
            return ListRunNodeStatusesResult(
                run_id=run.id,
                workflow_id=run.workflow_id,
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        all_tasks = await self.task_repo.alist_by_run(command.run_id)
        tasks_by_node: dict[str, list] = {}
        for t in all_tasks:
            tasks_by_node.setdefault(t.node_id, []).append(t)

        skipped = skipped_nodes_of(command.run_id, self._view_store, run)
        child_runs = child_runs_of(command.run_id, self._view_store, run)

        nodes: list[dict[str, Any]] = []
        for node in workflow.nodes:
            node_tasks = tasks_by_node.get(node.id, [])
            status = aggregate_node_status(node.id, node_tasks, skipped, run.status)

            started_at = _earliest(t.started_at for t in node_tasks)
            completed_at = _latest(t.completed_at for t in node_tasks) if status in ("COMPLETED", "FAILED", "SKIPPED") else None
            error = _last_error(node_tasks) if status == "FAILED" else None

            entry = NodeStatusEntry(
                node_id=node.id,
                node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                error=error,
                child_run_id=child_runs.get(node.id),
            )
            nodes.append(entry.__dict__)

        return ListRunNodeStatusesResult(
            run_id=run.id,
            workflow_id=run.workflow_id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            started_at=run.started_at,
            completed_at=run.completed_at,
            nodes=nodes,
        )


def _earliest(values: Any) -> str | None:
    vs = [v for v in values if v]
    return min(vs) if vs else None


def _latest(values: Any) -> str | None:
    vs = [v for v in values if v]
    return max(vs) if vs else None


def _last_error(tasks: Any) -> str | None:
    # Order by (completion, start, loop iteration), then take the last error.
    # ``Task`` has no ``iteration_index`` scalar — loop iteration lives in
    # ``scope_stack`` (mirrors ``get_node_execution_use_case``, which sorts the
    # same way). The previous ``t.iteration_index`` raised ``AttributeError``
    # and 500'd this endpoint for every run containing a FAILED node
    # (``_last_error`` is only called on FAILED-status nodes).
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            t.completed_at or "",
            t.started_at or "",
            t.scope_stack[-1].iter if t.scope_stack else 0,
        ),
    )
    for t in reversed(sorted_tasks):
        if t.error:
            return t.error
    return None
