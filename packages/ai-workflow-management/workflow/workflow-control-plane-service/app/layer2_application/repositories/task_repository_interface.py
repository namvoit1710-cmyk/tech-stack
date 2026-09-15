from typing import Protocol
from app.layer1_domain.entities.task import Task


class ITaskRepository(Protocol):
    def save(self, task: Task) -> None: ...
    def get_by_id(self, id: str) -> Task | None: ...
    def list_by_run(self, run_id: str) -> list[Task]: ...

    def latest_by_run_node(self, run_id: str, node_id: str) -> Task | None:
        """The most recently created task for one node of one run, or None.

        A point lookup, deliberately separate from ``list_by_run``. The
        dispatch-coalescing check (``OrchestrationEngine._active_task``) asks
        this question once per successor on every node completion, and used to
        answer it with ``list_by_run`` plus a Python scan — reading and
        deserializing every task of the run to return one of them. Because the
        run's task count grows as the run progresses, that made the cost of a
        completion grow with the run and the cost of a run quadratic in its
        node count (SA-2318).

        Ties on ``created_at`` are not ordered further; callers must not depend
        on which of two same-timestamp tasks comes back.
        """
        ...

    def update_status(self, id: str, status: str) -> None: ...
    def delete_all(self) -> int: ...
