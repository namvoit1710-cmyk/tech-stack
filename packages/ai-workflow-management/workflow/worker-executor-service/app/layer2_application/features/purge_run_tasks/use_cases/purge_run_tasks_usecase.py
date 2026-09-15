"""Purge a run's pull tasks (SA-2032, B7) — WCP calls this on cancel/reset.

Removing the rows makes every stale-run pull task un-leaseable and un-completable (a
late worker callback finds no row → rejected), which — together with the run_generation
echoed on every result event — is how STALE_RUN_TASK_LEASED is prevented. Idempotent:
purging a run with no tasks is a 0-count no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.ready_task_store_interface import IReadyTaskStore


@dataclass
class PurgeRunTasksInput:
    run_id: str = ""


class PurgeRunTasksUseCase:
    def __init__(
        self,
        logger: ILogger,
        ready_task_store: IReadyTaskStore | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.ready_task_store = ready_task_store

    def execute(self, input: PurgeRunTasksInput) -> dict:
        if self.ready_task_store is None or not input.run_id:
            return {"run_id": input.run_id, "purged": 0}
        purged = self.ready_task_store.purge_by_run(input.run_id)
        if purged:
            self.logger.info("Purged pull tasks for run", run_id=input.run_id, count=len(purged))
        return {"run_id": input.run_id, "purged": len(purged)}
