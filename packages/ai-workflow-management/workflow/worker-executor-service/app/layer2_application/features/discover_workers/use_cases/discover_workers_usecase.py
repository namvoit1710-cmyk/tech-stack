"""Discover workers use case."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer2_application.services.discovery_engine import DiscoveryEngine


@dataclass
class DiscoverWorkersInput:
    capability: str = ""


@dataclass
class DiscoverWorkersOutput:
    id: str = ""
    name: str = ""
    endpoint_url: str = ""
    health_status: str = ""
    last_heartbeat: str = ""
    capabilities: list[dict[str, str]] = field(default_factory=list)


class DiscoverWorkersUseCase:
    """Finds workers by capability."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: DiscoverWorkersInput) -> list[dict]:
        self.logger.info("Discovering workers", capability=input.capability)
        workers = DiscoveryEngine.find_workers(input.capability, self.registry_store)
        return [
            DiscoverWorkersOutput(
                id=w.id,
                name=w.name,
                endpoint_url=w.endpoint_url,
                health_status=w.health_status,
                last_heartbeat=w.last_heartbeat,
                capabilities=[{"domain": c.domain, "action": c.action} for c in w.capabilities],
            ).__dict__
            for w in workers
        ]
