"""List workflow runs with filtering and pagination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.value_objects.run_progress import (
    compute_progress_pct,
    count_terminal_nodes,
    reachable_path_total,
)
from app.layer2_application.interfaces.event_log_repository_interface import IEventLogRepository
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import IRunRepository, ListRunsFilter
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository

# SA-2650: statuses that carry a progress %. RUNNING = live progress;
# FAILED = how far the run got before it stopped. Terminal-success
# (COMPLETED) and CANCELLED/PAUSED carry no chip.
_PROGRESS_STATUSES = {"RUNNING", "FAILED"}


def _triggered_by_view(value: str | None) -> dict[str, Any]:
    """SA-2558: shape the stored trigger identity for the Execution-History chip.

    ``value`` is the label persisted at run creation (name -> email -> id, or
    the ``system`` sentinel). Automated / M2M / schedule runs — and historical
    rows created before the column existed (``None``) — are marked
    ``is_system`` so the FE renders a neutral "System" chip instead of a person.
    """
    v = (value or "").strip()
    is_system = (
        not v
        or v.lower() == "system"
        or v.lower().startswith("service-account-")
    )
    return {
        "id": v or "system",
        "label": "System" if is_system else v,
        "is_system": is_system,
    }


@dataclass
class ListRunsCommand:
    workflow_id: str | None = None
    statuses: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    is_test: bool | None = None
    roots_only: bool = False
    offset: int = 0
    limit: int = 20
    eq: dict[str, str] = field(default_factory=dict)
    ne: dict[str, str] = field(default_factory=dict)
    gt: dict[str, str] = field(default_factory=dict)
    ge: dict[str, str] = field(default_factory=dict)
    lt: dict[str, str] = field(default_factory=dict)
    le: dict[str, str] = field(default_factory=dict)
    contains: dict[str, str] = field(default_factory=dict)


@dataclass
class ListRunsResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 20


class ListRunsUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        workflow_repo: IWorkflowRepository,
        run_context_view_store: IRunContextViewStore | None = None,
        event_log_repo: IEventLogRepository | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.workflow_repo = workflow_repo
        # SA-2650: read-side view (topology → total nodes) + event log
        # (successful/failed/skipped breakdown) used to derive run-progress %
        # for RUNNING/FAILED rows. Optional so the feature still wires when a
        # store isn't available (tests).
        self._view_store = run_context_view_store
        self._event_log_repo = event_log_repo

    async def execute(self, command: ListRunsCommand) -> ListRunsResult:
        self.logger.info("Listing runs", workflow_id=command.workflow_id)

        limit = max(1, min(command.limit, 100))
        offset = max(0, command.offset)

        # Pre-filter workflow IDs when contains(workflow_name, ...) is used
        contains_for_repo = dict(command.contains)
        workflow_ids_filter = None
        workflow_name_search = contains_for_repo.pop("workflow_name", None)
        if workflow_name_search:
            search_lower = workflow_name_search.lower()
            all_workflows = await self.workflow_repo.alist()
            matching_ids = [w.id for w in all_workflows if search_lower in (w.name or "").lower()]
            if not matching_ids:
                return ListRunsResult(items=[], total=0, offset=offset, limit=limit)
            workflow_ids_filter = matching_ids

        filters = ListRunsFilter(
            workflow_id=command.workflow_id,
            statuses=command.statuses,
            tenant_id=command.tenant_id,
            is_test=command.is_test,
            roots_only=command.roots_only,
            offset=offset,
            limit=limit,
            workflow_ids=workflow_ids_filter,
            eq=command.eq,
            ne=command.ne,
            gt=command.gt,
            ge=command.ge,
            lt=command.lt,
            le=command.le,
            contains=contains_for_repo,
        )

        page = self.run_repo.list_runs(filters)

        workflow_ids = {r.workflow_id for r in page.items}
        name_map: dict[str, str] = {}
        # SA-2650: node totals per workflow, so a RUNNING run can report
        # progress = (completed + skipped) ÷ total_nodes.
        node_total_map: dict[str, int] = {}
        for wf_id in workflow_ids:
            wf = await self.workflow_repo.aget_by_id(wf_id)
            if wf:
                name_map[wf_id] = wf.name
                node_total_map[wf_id] = len(getattr(wf, "nodes", []) or [])

        items = []
        for run in page.items:
            status = run.status.value if hasattr(run.status, "value") else str(run.status)
            item = {
                "id": run.id,
                "workflow_id": run.workflow_id,
                "workflow_name": name_map.get(run.workflow_id, ""),
                "status": status,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "error": run.error,
                "is_test": run.is_test,
                "tenant_id": run.tenant_id,
                "parent_run_id": run.parent_run_id,
                "root_run_id": run.root_run_id,
                "depth_level": run.depth_level,
                # SA-2558: server-verified trigger identity for the "triggered
                # by" chip. FE renders only — no lookup on the client.
                "triggered_by": _triggered_by_view(run.triggered_by),
            }
            # SA-2650: live progress only for RUNNING rows — the FE renders a
            # yellow % chip on those; terminal/paused rows carry null and show
            # no chip.
            item.update(self._progress_fields(run.id, status, node_total_map.get(run.workflow_id, 0)))
            items.append(item)

        return ListRunsResult(
            items=items,
            total=page.total,
            offset=offset,
            limit=limit,
        )

    def _progress_fields(self, run_id: str, status: str, fallback_total: int) -> dict[str, Any]:
        """SA-2650: progress for a RUNNING or FAILED run, else null fields.

        RUNNING shows live progress; FAILED shows how far it got before it
        stopped (same numerator ÷ total). Reads completed/skipped node counts
        from the run projection view and divides by the run's total node count.
        Total comes from the view's ``topology`` (the exact graph this run
        executed, version-accurate), falling back to the current workflow's
        node count. Other statuses (and any run without a resolvable
        view/topology) carry ``None`` so the FE shows no chip.
        """
        empty = {"progress_pct": None, "completed_nodes": None, "total_nodes": None}
        if str(status).upper() not in _PROGRESS_STATUSES or self._view_store is None:
            return empty
        try:
            view = self._view_store.get(run_id)
        except Exception:  # pragma: no cover - defensive; view store is best-effort
            view = None
        if view is None:
            return empty
        # SA-2650: denominator = the path this run is actually taking (drops
        # un-taken CONDITION/SWITCH branches via last_taken_edge_ports), so a
        # branching run reflects real progress and reaches 100%.
        topology = getattr(view, "topology", None)
        total = reachable_path_total(topology, getattr(view, "last_taken_edge_ports", None)) or fallback_total
        if total <= 0:
            return empty
        # Count SUCCESSFUL completions + skips from the event log — failed nodes
        # are excluded so a FAILED run never reads as 100% (100% == genuinely
        # done). Fall back to the projection's lumped count if no event log.
        if self._event_log_repo is not None:
            try:
                completed, _failed, skipped = count_terminal_nodes(
                    self._event_log_repo.list_by_run(run_id),
                )
            except Exception:  # pragma: no cover - defensive
                completed, skipped = len(view.completed_nodes), len(view.skipped_nodes)
        else:
            completed, skipped = len(view.completed_nodes), len(view.skipped_nodes)
        done = completed + skipped
        return {
            "progress_pct": compute_progress_pct(completed, skipped, total),
            "completed_nodes": done,
            "total_nodes": total,
        }
