import operator

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.repositories.run_repository_interface import (
    IRunRepository,
    ListRunsFilter,
    ListRunsPage,
    OptimisticConcurrencyError,
)
from app.layer1_domain.value_objects.run_status import RunStatus
from typing import Any


_FILTERABLE = frozenset({
    "id", "workflow_id", "workflow_version", "status",
    "parent_run_id", "parent_node_id", "depth_level", "root_run_id",
    "error", "started_at", "completed_at", "tenant_id",
    "correlation_id", "callback_topic", "is_test", "test_mode",
    "execution_mode", "version", "generation",
})
_CONTAINS_FILTERABLE = frozenset({
    "id", "workflow_id", "workflow_version", "status",
    "parent_run_id", "parent_node_id", "root_run_id",
    "error", "tenant_id", "correlation_id", "callback_topic",
    "test_mode", "execution_mode",
})
_OP_FNS = {
    "eq": operator.eq, "ne": operator.ne,
    "gt": operator.gt, "ge": operator.ge,
    "lt": operator.lt, "le": operator.le,
}


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryRunRepository(AsyncRepoMixin, IRunRepository):
    def __init__(self) -> None:
        self._store: dict[str, WorkflowRun] = {}

    def save(self, run: WorkflowRun) -> None:
        """Phase M.2.1 CAS: compare ``run.version`` against the stored
        version, raise on mismatch, increment in place on success.
        First insert is unconditional and sets ``run.version = 1``.
        """
        existing = self._store.get(run.id)
        if existing is not None and existing.version != run.version:
            raise OptimisticConcurrencyError(
                run.id, expected=run.version, actual=existing.version,
            )
        run.version = (existing.version if existing is not None else 0) + 1
        self._store[run.id] = run

    def get_by_id(self, id: str) -> WorkflowRun | None:
        return self._store.get(id)

    # ------------------------------------------------------------------
    # Async wrappers — in-memory work isn't blocking, so these just call
    # the sync methods. They satisfy ``IRunRepository``'s async contract
    # so ``save_run_with_cas_retry`` can use the same code path against
    # memory repos in tests and against the HANA repo in production.
    # ------------------------------------------------------------------

    async def aget_by_id(self, id: str) -> WorkflowRun | None:
        return self.get_by_id(id)

    async def asave(self, run: WorkflowRun) -> None:
        self.save(run)

    def get_by_root_id(self, root_id: str) -> list[WorkflowRun]:
        return [r for r in self._store.values() if r.root_run_id == root_id]

    def update_status(self, id: str, status: RunStatus) -> None:
        run = self._store.get(id)
        if run:
            run.status = status

    def list_by_workflow(self, workflow_id: str) -> list[WorkflowRun]:
        return [r for r in self._store.values() if r.workflow_id == workflow_id]

    def list_runs(self, filters: ListRunsFilter) -> ListRunsPage:
        runs = list(self._store.values())

        if filters.workflow_id:
            runs = [r for r in runs if r.workflow_id == filters.workflow_id]
        if filters.workflow_ids is not None:
            allowed = set(filters.workflow_ids)
            runs = [r for r in runs if r.workflow_id in allowed]
        if filters.statuses:
            runs = [r for r in runs if (r.status.value if hasattr(r.status, "value") else str(r.status)) in filters.statuses]
        if filters.tenant_id is not None:
            runs = [r for r in runs if r.tenant_id == filters.tenant_id]
        if filters.is_test is not None:
            runs = [r for r in runs if r.is_test == filters.is_test]
        if filters.roots_only:
            runs = [r for r in runs if not r.parent_run_id]
        for op_name in ("eq", "ne", "gt", "ge", "lt", "le"):
            for attr, val in (getattr(filters, op_name) or {}).items():
                if attr not in _FILTERABLE:
                    continue
                runs = [r for r in runs if self._matches(r, attr, op_name, val)]
        for attr, search in (filters.contains or {}).items():
            if attr not in _CONTAINS_FILTERABLE:
                continue
            search_lower = search.lower()
            runs = [
                r for r in runs
                if search_lower in self._attr_to_str(r, attr).lower()
            ]

        runs.sort(key=lambda r: r.started_at or "", reverse=True)
        total = len(runs)
        paginated = runs[filters.offset : filters.offset + filters.limit]

        return ListRunsPage(items=paginated, total=total)

    @classmethod
    def _matches(cls, run: Any, attr: str, op_name: str, val: str) -> bool:
        op_fn = _OP_FNS[op_name]
        actual = getattr(run, attr, None)
        if hasattr(actual, "value"):
            actual = actual.value
        if isinstance(actual, bool):
            return op_fn(actual, str(val).lower() in ("true", "1"))
        if isinstance(actual, int):
            try:
                return op_fn(actual, int(val))
            except (ValueError, TypeError):
                return False
        return op_fn(str(actual or ""), str(val))

    @staticmethod
    def _attr_to_str(obj: Any, field: str) -> str:
        val = getattr(obj, field, "")
        if hasattr(val, "value"):
            val = val.value
        return str(val or "")

    def delete_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count
