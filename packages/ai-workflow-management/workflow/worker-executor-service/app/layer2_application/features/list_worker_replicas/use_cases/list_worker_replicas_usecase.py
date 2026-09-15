"""SA-2053 (R6) — per-type replica aggregate.

Counts come from the store's replica_summary(), which is the ONE definition of
"how many replicas" (DISTINCT instance_id, never rows). This use case does not
re-derive them: a second counting rule is free to drift from the store's, and the
whole SA-2047 epic exists because two halves of one idea drifted apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.value_objects.replica_aggregation import (
    oldest_live_age, representative_mode,
)
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class WorkerReplicaSummaryOutput:
    worker_type: str = ""
    delivery_mode: str = ""            # wire value; "" = undeterminable
    replicas_total: int = 0
    replicas_healthy: int = 0          # count_healthy -> HEALTHY_STATUSES, admits blank status
    # Q13 population: health_status in LIVE_STATUSES ("healthy", "active") AND age <=
    # WORKER_STALE_AFTER_SECONDS. NARROWER than replicas_healthy: HEALTHY_STATUSES admits
    # a blank/never-heartbeated status, so that instance is counted in replicas_healthy but
    # excluded here -- an unmeasurable heartbeat cannot be the worst measurement. None means
    # UNKNOWN (nobody qualified), never 0 -- 0 would read as "every replica just heartbeated".
    oldest_heartbeat_age_seconds: Optional[float] = None


class ListWorkerReplicasUseCase:
    """One row per worker_type: how many replicas, how many healthy, how stale.

    Reads only. No tenant filter and no lease data: a WorkerRegistration carries no
    tenant (registration hardcodes "system") and lease state lives in a different,
    PULL-only store with no lease->instance attribution. Both were cut from SA-2053
    with the reason recorded, and handed to R7/R9. Do not add them here.
    """

    def __init__(self, logger: ILogger, registry_store: IWorkerRegistryStore,
                 app_config: Any = None, **kwargs: Any) -> None:
        self.logger = logger
        self.registry_store = registry_store
        self.app_config = app_config

    def execute(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        # int(getattr(...)) NOT `getattr(...) or 120`: `or` swallows a configured 0
        # and silently substitutes 120 (the SA-2052 Minor #4 defect).
        stale_after = int(getattr(self.app_config, "WORKER_STALE_AFTER_SECONDS", 120)
                          if self.app_config is not None else 120)
        summary = self.registry_store.replica_summary()
        out: list[dict] = []
        for worker_type, counts in summary.items():
            # A SECOND lock acquisition. R4's sweeper can empty this type between
            # the summary above and this call -- see representative_mode's "" branch.
            instances = self.registry_store.list_instances(worker_type)
            out.append(WorkerReplicaSummaryOutput(
                worker_type=worker_type,
                delivery_mode=representative_mode(instances),
                replicas_total=counts.get("total", 0),
                replicas_healthy=counts.get("healthy", 0),
                oldest_heartbeat_age_seconds=oldest_live_age(instances, stale_after, now),
            ).__dict__)
        self.logger.info(f"Listing replica summary for {len(out)} worker_types")
        return out
