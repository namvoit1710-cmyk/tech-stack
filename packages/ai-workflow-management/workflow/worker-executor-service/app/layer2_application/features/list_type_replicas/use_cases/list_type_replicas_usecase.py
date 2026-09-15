"""SA-2053 (R6) — per-instance replica detail for one worker_type.

A pure read over the registry: one row per replica PROCESS of the type, carrying
the identity SA-2048 added and SA-2055 populates. Adds no field, no store method,
no write path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.value_objects.worker_liveness import age_seconds
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class ListTypeReplicasInput:
    worker_type: str = ""


@dataclass
class WorkerInstanceOutput:
    instance_id: str = ""
    name: str = ""               # WorkerRegistration.name (human worker name)
    status: str = ""              # WorkerRegistration.health_status
    last_heartbeat: str = ""
    age_seconds: Optional[float] = None   # None = unknown, never 0
    host: str = ""
    version: str = ""
    sdk_version: str = ""
    started_at: str = ""
    # The WIRE value ("push"/"pull"), taken via .value. DeliveryMode subclasses str
    # so `== "push"` works, but Enum.__str__ still wins: str() and f-strings emit
    # "DeliveryMode.PUSH". __post_init__ normalizes the entity field, so .value is total.
    delivery_mode: str = ""


class ListTypeReplicasUseCase:
    """Every registered instance of one worker_type -- healthy or not.

    Unhealthy and stale-but-not-yet-GC'd instances ARE listed: this is the detail
    view, and seeing the corpse is the point. The aggregate endpoint
    (list_worker_replicas) is where liveness filtering happens.
    """

    def __init__(self, logger: ILogger, registry_store: IWorkerRegistryStore, **kwargs: Any) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: ListTypeReplicasInput, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        worker_type = (input.worker_type or "").strip()
        if not worker_type:
            return []
        instances = self.registry_store.list_instances(worker_type)
        self.logger.info(f"Listing replicas of worker_type={worker_type}: {len(instances)}")
        return [
            WorkerInstanceOutput(
                instance_id=w.instance_id,
                name=w.name,
                status=w.health_status,
                last_heartbeat=w.last_heartbeat,
                age_seconds=age_seconds(w.last_heartbeat, now),
                host=w.host,
                version=w.version,
                sdk_version=w.sdk_version,
                started_at=w.started_at,
                delivery_mode=w.delivery_mode.value,
            ).__dict__
            for w in instances
        ]
