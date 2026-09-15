from app.layer1_domain.entities.stage_record import StageRecord


class MemoryStageHistoryRepository:
    def __init__(self) -> None:
        self._store: list[StageRecord] = []

    def save(self, record: StageRecord) -> None:
        self._store.append(record)

    def list_by_run(self, run_id: str) -> list[StageRecord]:
        return [r for r in self._store if r.run_id == run_id]

    def list_by_task(self, task_id: str) -> list[StageRecord]:
        return [r for r in self._store if r.task_id == task_id]

    def delete_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count
