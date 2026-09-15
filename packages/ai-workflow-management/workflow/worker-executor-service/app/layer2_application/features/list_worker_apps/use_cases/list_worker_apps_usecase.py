"""SA-2047 R14 — per-APP aggregate. One row per deployable.

The sibling of list_worker_replicas (R6), on the other identity axis: that one
answers "how many replicas of this NODE TYPE", this one answers "how many
workers do we run, and are they alive". Both read the store's counts; neither
re-derives them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.value_objects.replica_aggregation import oldest_live_age, representative_mode
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class WorkerAppSummaryOutput:
    worker_app: str = ""
    delivery_mode: str = ""
    nodes_total: int = 0            # DISTINCT node types (jira-worker offers 22,
                                    # and still 22 when it scales to 2 replicas)
    nodes_healthy: int = 0          # of those, servable by at least one live replica
    # The names behind nodes_total, sorted. Ships with the summary so the UI can
    # list an app's node types without a per-app instances round trip — the
    # popover used to read them off the lazily-fetched instance list and so came
    # up empty on any row the user had not expanded first.
    node_types: list[str] = field(default_factory=list)
    replicas_total: int = 0         # PROCESSES; 0 and meaningless when identity is pending
    replicas_healthy: int = 0
    # "known" -> the counts above are real. "pending" -> the registry was never
    # told which process each registration came from, so the counts are 0 and the
    # consumer MUST NOT render them. 0 would read as "no replicas", also false.
    replica_identity: str = "known"
    oldest_heartbeat_age_seconds: Optional[float] = None


class ListWorkerAppsUseCase:
    def __init__(self, logger: ILogger, registry_store: IWorkerRegistryStore,
                 app_config: Any = None, **kwargs: Any) -> None:
        self.logger = logger
        self.registry_store = registry_store
        self.app_config = app_config

    def execute(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        stale_after = int(getattr(self.app_config, "WORKER_STALE_AFTER_SECONDS", 120)
                          if self.app_config is not None else 120)
        summary = self.registry_store.app_summary()
        out: list[dict] = []
        for worker_app, counts in summary.items():
            registrations = [reg for reg, _nodes in self.registry_store.list_app_instances(worker_app)]
            pending = bool(counts.get("all_synthesized"))
            out.append(WorkerAppSummaryOutput(
                worker_app=worker_app,
                delivery_mode=representative_mode(registrations),
                nodes_total=counts.get("nodes_total", 0),
                nodes_healthy=counts.get("nodes_healthy", 0),
                node_types=list(counts.get("node_types", [])),
                replicas_total=0 if pending else counts.get("replicas_total", 0),
                replicas_healthy=0 if pending else counts.get("replicas_healthy", 0),
                replica_identity="pending" if pending else "known",
                oldest_heartbeat_age_seconds=oldest_live_age(registrations, stale_after, now),
            ).__dict__)
        self.logger.info(f"Listing app summary for {len(out)} worker apps")
        return out
