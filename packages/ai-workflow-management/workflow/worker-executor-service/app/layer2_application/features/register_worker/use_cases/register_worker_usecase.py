"""Register worker use case."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability


@dataclass
class RegisterWorkerInput:
    name: str = ""
    capabilities: list[dict[str, str]] = field(default_factory=list)
    endpoint_url: str = ""
    ttl_seconds: int = 60


@dataclass
class RegisterWorkerOutput:
    id: str = ""
    name: str = ""
    endpoint_url: str = ""
    health_status: str = ""
    last_heartbeat: str = ""
    capabilities: list[dict[str, str]] = field(default_factory=list)


class RegisterWorkerUseCase:
    """Registers a new worker in the registry."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: RegisterWorkerInput) -> dict:
        self.logger.info("Registering worker", name=input.name)

        worker = WorkerRegistration(
            name=input.name,
            capabilities=[WorkerCapability(**c) for c in (input.capabilities or [])],
            endpoint_url=input.endpoint_url,
            ttl_seconds=input.ttl_seconds,
        )

        self.registry_store.register(worker)

        return RegisterWorkerOutput(
            id=worker.id,
            name=worker.name,
            endpoint_url=worker.endpoint_url,
            health_status=worker.health_status,
            last_heartbeat=worker.last_heartbeat,
            capabilities=[{"domain": c.domain, "action": c.action} for c in worker.capabilities],
        ).__dict__
