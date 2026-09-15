"""Worker heartbeat use case."""

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


@dataclass
class WorkerHeartbeatInput:
    worker_id: str = ""
    status: str = "healthy"


@dataclass
class WorkerHeartbeatOutput:
    acknowledged: bool = False
    re_register: bool = False


class WorkerHeartbeatUseCase:
    """Acknowledges a worker heartbeat by updating the registry's last-seen timestamp."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: WorkerHeartbeatInput) -> dict:
        self.logger.info("Worker heartbeat", worker_id=input.worker_id)
        found = self.registry_store.heartbeat(input.worker_id, input.status)
        if not found:
            self.logger.info(
                "Worker unknown, signaling re-registration",
                worker_id=input.worker_id,
            )
            return WorkerHeartbeatOutput(acknowledged=False, re_register=True).__dict__
        return WorkerHeartbeatOutput(acknowledged=True).__dict__
