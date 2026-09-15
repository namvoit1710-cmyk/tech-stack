from app.layer1_domain.entities.task import Task
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryTaskRepository(AsyncRepoMixin, ITaskRepository):
    def __init__(self) -> None:
        self._store: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        self._store[task.id] = task

    def get_by_id(self, id: str) -> Task | None:
        return self._store.get(id)

    def list_by_run(self, run_id: str) -> list[Task]:
        return [t for t in self._store.values() if t.run_id == run_id]

    def latest_by_run_node(self, run_id: str, node_id: str) -> Task | None:
        """Newest task for one node of one run.

        Deliberately the same comparison the HANA repo pushes into SQL: None
        coalesced to "" so a timestamp-less task sorts oldest, and strictly-
        greater so the first of two equal timestamps wins.
        """
        latest: Task | None = None
        for t in self._store.values():
            if t.run_id != run_id or t.node_id != node_id:
                continue
            if latest is None or (t.created_at or "") > (latest.created_at or ""):
                latest = t
        return latest

    def update_status(self, id: str, status: TaskStatus) -> None:
        task = self._store.get(id)
        if task:
            task.status = status

    def delete_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count
