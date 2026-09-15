"""Get worker use case."""

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


@dataclass
class GetWorkerInput:
    worker_id: str = ""


@dataclass
class GetWorkerOutput:
    id: str = ""
    name: str = ""
    endpoint_url: str = ""
    health_status: str = ""
    last_heartbeat: str = ""
    capabilities: list[dict[str, str]] = field(default_factory=list)
    worker_type: str = ""
    version: str = ""
    sdk_version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"in": [], "out": []})


class GetWorkerUseCase:
    """Gets a specific worker by ID from the registry."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: GetWorkerInput) -> dict:
        self.logger.info("Getting worker", worker_id=input.worker_id)
        worker = self.registry_store.get(input.worker_id)
        if not worker:
            # Fallback: treat worker_id as worker_type
            worker = self.registry_store.get_by_worker_type(input.worker_id)
        if not worker:
            return {"error": "Worker not found"}
        return GetWorkerOutput(
            id=worker.id,
            name=worker.name,
            endpoint_url=worker.endpoint_url,
            health_status=worker.health_status,
            last_heartbeat=worker.last_heartbeat,
            capabilities=[{"domain": c.domain, "action": c.action} for c in worker.capabilities],
            worker_type=worker.worker_type,
            version=worker.version,
            sdk_version=worker.sdk_version,
            input_schema=worker.input_schema,
            output_schema=worker.output_schema,
            description=worker.description,
            node_class=worker.node_class,
            icon=worker.icon,
            color=worker.color,
            tags=worker.tags,
            ports=worker.ports,
        ).__dict__
