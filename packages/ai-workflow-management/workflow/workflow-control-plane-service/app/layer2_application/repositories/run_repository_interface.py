from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.workflow_run import WorkflowRun


class OptimisticConcurrencyError(Exception):
    """Raised by ``IRunRepository.save`` when another caller's save
    landed between this caller's load and save. Phase M.2.1 — see
    ``docs/design/phase-m2-optimistic-cas.md``.

    ``expected`` is the version the caller held (``run.version``).
    ``actual`` is the version observed in the store at save time
    (or ``-1`` when the row has since been deleted / is unknown).
    """

    def __init__(self, run_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Run {run_id!r}: expected version {expected}, found {actual}"
        )
        self.run_id = run_id
        self.expected = expected
        self.actual = actual


@dataclass
class ListRunsFilter:
    workflow_id: str | None = None
    statuses: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    is_test: bool | None = None
    roots_only: bool = False  # when True, return only top-level runs (PARENT_RUN_ID IS NULL)
    offset: int = 0
    limit: int = 20
    workflow_ids: list[str] | None = None
    eq: dict[str, str] = field(default_factory=dict)
    ne: dict[str, str] = field(default_factory=dict)
    gt: dict[str, str] = field(default_factory=dict)
    ge: dict[str, str] = field(default_factory=dict)
    lt: dict[str, str] = field(default_factory=dict)
    le: dict[str, str] = field(default_factory=dict)
    contains: dict[str, str] = field(default_factory=dict)


@dataclass
class ListRunsPage:
    items: list[WorkflowRun] = field(default_factory=list)
    total: int = 0


class IRunRepository(Protocol):
    def save(self, run: WorkflowRun) -> None:
        """Persist ``run`` with optimistic-concurrency semantics.

        On success, ``run.version`` is incremented **in place** so the
        caller's reference reflects the newly-persisted version and
        can be used for a subsequent save in the same transaction.

        Raises ``OptimisticConcurrencyError`` when the persisted
        version for ``run.id`` does not match ``run.version`` at the
        time of the call — i.e. another caller saved between this
        caller's ``get_by_id`` and ``save``.

        First-time inserts (no row exists for ``run.id``) succeed
        unconditionally and set ``run.version = 1`` regardless of
        the caller-supplied value.
        """
    def get_by_id(self, id: str) -> WorkflowRun | None: ...
    def get_by_id_version(self, id: str, workflow_version: str) -> WorkflowRun | None: ...
    def get_by_root_id(self, root_id: str) -> list[WorkflowRun]: ...
    def update_status(self, id: str, status: str) -> None: ...
    def list_by_workflow(self, workflow_id: str) -> list[WorkflowRun]: ...
    def list_runs(self, filters: ListRunsFilter) -> ListRunsPage: ...
    def delete_all(self) -> int: ...

    # Async wrappers used by ``save_run_with_cas_retry`` so the helper
    # offloads the SELECT VERSION + CAS UPDATE to a worker thread under
    # ``ASYNC_HANA_ENABLED``. Sync ``save`` / ``get_by_id`` stay
    # available for migrations / startup paths.
    async def asave(self, run: WorkflowRun) -> None: ...
    async def aget_by_id(self, id: str) -> WorkflowRun | None: ...
    async def aget_by_id_version(self, id: str, workflow_version: str) -> WorkflowRun | None: ...
