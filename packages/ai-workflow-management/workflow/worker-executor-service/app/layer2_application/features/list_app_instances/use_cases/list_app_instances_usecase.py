"""SA-2047 R14 — per-instance detail for one worker APP.

The sibling of list_type_replicas (R6): same row shape, plus the node types the
process serves, and folded by process rather than listed per node type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.value_objects.worker_liveness import age_seconds
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class ListAppInstancesInput:
    worker_app: str = ""


@dataclass
class AppInstanceOutput:
    instance_id: str = ""
    status: str = ""
    last_heartbeat: str = ""
    age_seconds: Optional[float] = None
    host: str = ""
    version: str = ""
    sdk_version: str = ""
    started_at: str = ""
    delivery_mode: str = ""
    nodes: list[str] = field(default_factory=list)


class ListAppInstancesUseCase:
    def __init__(self, logger: ILogger, registry_store: IWorkerRegistryStore, **kwargs: Any) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: ListAppInstancesInput, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        worker_app = (input.worker_app or "").strip()
        if not worker_app:
            return []
        rows = self.registry_store.list_app_instances(worker_app)
        self.logger.info(f"Listing instances of worker_app={worker_app}: {len(rows)}")
        return [
            AppInstanceOutput(
                instance_id=w.instance_id,
                status=w.health_status,
                last_heartbeat=w.last_heartbeat,
                age_seconds=age_seconds(w.last_heartbeat, now),
                host=w.host,
                version=w.version,
                sdk_version=w.sdk_version,
                started_at=w.started_at,
                delivery_mode=w.delivery_mode.value,
                nodes=sorted(nodes),
            ).__dict__
            for w, nodes in rows
        ]
