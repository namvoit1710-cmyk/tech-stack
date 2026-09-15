"""List sub-workflow runs spawned by a parent run.

Lightweight read-only endpoint that the frontend uses to drill into a
WORKFLOW-type node. Source of truth: ``child_runs_of`` (the projector,
mapping parent_node_id -> child_run_id). For each child the run's current
status and timing are projected.
"""

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.value_objects.run_progress import (
    compute_progress_pct,
    count_terminal_nodes,
    reachable_path_total,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.event_log_repository_interface import IEventLogRepository
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository

# SA-2650: statuses that carry a progress % (RUNNING = live, FAILED = how far
# it got). Kept in sync with list_runs_use_case._PROGRESS_STATUSES.
_PROGRESS_STATUSES = {"RUNNING", "FAILED"}


@dataclass
class ListRunChildrenCommand:
    run_id: str = ""


@dataclass
class ChildRunEntry:
    parent_node_id: str = ""
    child_run_id: str = ""
    workflow_id: str = ""
    status: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    # SA-2650: live progress for a RUNNING subflow row (null otherwise).
    progress_pct: int | None = None
    completed_nodes: int | None = None
    total_nodes: int | None = None


@dataclass
class ListRunChildrenResult:
    run_id: str = ""
    children: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListRunChildrenUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        run_context_view_store: IRunContextViewStore | None = None,
        workflow_repo: IWorkflowRepository | None = None,
        event_log_repo: IEventLogRepository | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self._view_store = run_context_view_store
        # SA-2650: used to resolve a subflow's total node count for progress.
        self.workflow_repo = workflow_repo
        # SA-2650: event log → successful/failed/skipped breakdown (failed
        # excluded from progress so a failed subflow never reads as 100%).
        self._event_log_repo = event_log_repo

    async def execute(self, command: ListRunChildrenCommand) -> ListRunChildrenResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return ListRunChildrenResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        children: list[dict[str, Any]] = []
        for parent_node_id, child_run_id in child_runs_of(
            command.run_id, self._view_store, run,
        ).items():
            child = await self.run_repo.aget_by_id(child_run_id)
            if child is None:
                # Child run id recorded in context but record missing — surface a
                # PENDING placeholder so the frontend at least sees the link.
                children.append(ChildRunEntry(
                    parent_node_id=parent_node_id,
                    child_run_id=child_run_id,
                    status="PENDING",
                ).__dict__)
                continue
            status = child.status.value if hasattr(child.status, "value") else str(child.status)
            progress = await self._progress_fields(child.id, status, child.workflow_id)
            children.append(ChildRunEntry(
                parent_node_id=parent_node_id,
                child_run_id=child.id,
                workflow_id=child.workflow_id,
                status=status,
                started_at=child.started_at,
                completed_at=child.completed_at,
                error=child.error,
                **progress,
            ).__dict__)

        return ListRunChildrenResult(run_id=run.id, children=children)

    async def _progress_fields(self, run_id: str, status: str, workflow_id: str) -> dict[str, Any]:
        """SA-2650: progress for a RUNNING or FAILED subflow, else null fields.

        RUNNING = live progress; FAILED = how far it got before stopping.
        Total node count comes from the run view's ``topology`` (version-
        accurate), falling back to the current workflow's node count.
        """
        empty = {"progress_pct": None, "completed_nodes": None, "total_nodes": None}
        if str(status).upper() not in _PROGRESS_STATUSES or self._view_store is None:
            return empty
        try:
            view = self._view_store.get(run_id)
        except Exception:  # pragma: no cover - defensive; best-effort read side
            view = None
        if view is None:
            return empty
        # SA-2650: reachable-path denominator (drops un-taken branches).
        topology = getattr(view, "topology", None)
        total = reachable_path_total(topology, getattr(view, "last_taken_edge_ports", None))
        if total <= 0 and self.workflow_repo is not None:
            try:
                wf = await self.workflow_repo.aget_by_id(workflow_id)
                total = len(getattr(wf, "nodes", []) or []) if wf else 0
            except Exception:  # pragma: no cover - defensive
                total = 0
        if total <= 0:
            return empty
        # Successful completions + skips from the event log (failed excluded).
        if self._event_log_repo is not None:
            try:
                completed, _failed, skipped = count_terminal_nodes(
                    self._event_log_repo.list_by_run(run_id),
                )
            except Exception:  # pragma: no cover - defensive
                completed, skipped = len(view.completed_nodes), len(view.skipped_nodes)
        else:
            completed, skipped = len(view.completed_nodes), len(view.skipped_nodes)
        return {
            "progress_pct": compute_progress_pct(completed, skipped, total),
            "completed_nodes": completed + skipped,
            "total_nodes": total,
        }
