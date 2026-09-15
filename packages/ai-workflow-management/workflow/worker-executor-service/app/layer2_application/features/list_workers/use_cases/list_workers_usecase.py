"""List workers use case."""

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.value_objects.node_kind import NodeKind


@dataclass
class ListWorkersOutput:
    id: str = ""
    name: str = ""
    endpoint_url: str = ""
    health_status: str = ""
    last_heartbeat: str = ""
    capabilities: list[dict[str, str]] = field(default_factory=list)
    worker_type: str = ""
    version: str = ""
    spec_version: str = "1.0.0"
    # Same NodeKind as the store entity (SA-1734 app-layer fix): keeps this DTO
    # honest about the value it actually carries, and lets jsonable_encoder /
    # proto assignment stringify it correctly instead of an f-string/str()
    # producing "NodeKind.ACTION".
    kind: NodeKind = NodeKind.ACTION
    sdk_version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"in": [], "out": []})
    # SA-2424: process identity so the HANA mirror can distinguish instances (esp.
    # two same-index replicas on one host) — host + pid + started_at. These live on
    # the registry entity but were dropped from this DTO, so the mirror stored NULLs.
    host: str = ""
    pid: int = 0
    started_at: str = ""


class ListWorkersUseCase:
    """Lists all registered workers."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self) -> list[dict]:
        self.logger.info("Listing all workers")
        workers = self.registry_store.list_all()
        return [
            ListWorkersOutput(
                id=w.id,
                name=w.name,
                endpoint_url=w.endpoint_url,
                health_status=w.health_status,
                last_heartbeat=w.last_heartbeat,
                capabilities=[{"domain": c.domain, "action": c.action} for c in w.capabilities],
                worker_type=w.worker_type,
                version=w.version,
                spec_version=getattr(w, "spec_version", "1.0.0") or "1.0.0",
                kind=getattr(w, "kind", NodeKind.ACTION) or NodeKind.ACTION,
                sdk_version=w.sdk_version,
                input_schema=w.input_schema,
                output_schema=w.output_schema,
                description=w.description,
                node_class=w.node_class,
                icon=w.icon,
                color=w.color,
                tags=w.tags,
                ports=w.ports,
                host=getattr(w, "host", "") or "",
                pid=getattr(w, "pid", 0) or 0,
                started_at=getattr(w, "started_at", "") or "",
            ).__dict__
            for w in workers
        ]
