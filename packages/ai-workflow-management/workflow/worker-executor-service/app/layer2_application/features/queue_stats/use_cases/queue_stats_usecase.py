"""Queue-stats use case (SA-2034, B9) — the pull autoscale / scale-from-zero signal.

A KEDA (or any) autoscaler polls GET /workers/type/{type}/queue-stats and scales the
pull worker deployment on `admitted_depth` — including UP FROM ZERO, since a
scaled-to-zero worker isn't long-polling to create demand any other way. Combined with
long-poll + the max-ready-TTL WORKER_UNAVAILABLE terminal (B8), a never-scaling worker
can't silently hang a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.ready_task_store_interface import IReadyTaskStore


@dataclass
class QueueStatsInput:
    worker_type: str = ""
    tenant: str = ""


class QueueStatsUseCase:
    def __init__(
        self,
        logger: ILogger,
        ready_task_store: IReadyTaskStore | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.ready_task_store = ready_task_store

    def execute(self, input: QueueStatsInput) -> dict:
        if self.ready_task_store is None or not input.worker_type:
            # Pull off / no type → a zeroed, valid signal (autoscaler reads 0 → no scale).
            return {
                "worker_type": input.worker_type, "tenant": input.tenant,
                "admitted_depth": 0, "leased": 0,
                "oldest_ready_age_seconds": 0.0, "bytes": 0,
            }
        return self.ready_task_store.queue_stats(input.worker_type, input.tenant)
