"""Deregister worker use case."""

from dataclasses import dataclass
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.message_broker_interface import IMessageBrokerClient
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


CATALOG_CHANGED_TOPIC = "catalog.changed"


@dataclass
class DeregisterWorkerInput:
    worker_id: str = ""


@dataclass
class DeregisterWorkerOutput:
    deregistered: bool = False


class DeregisterWorkerUseCase:
    """Removes a worker from the registry."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        message_broker_client: Optional[IMessageBrokerClient] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store
        self.message_broker_client = message_broker_client

    def execute(self, input: DeregisterWorkerInput) -> dict:
        self.logger.info("Deregistering worker", worker_id=input.worker_id)
        # Snapshot worker_type/spec_version before we lose the record, so the
        # event downstream can identify which node is affected.
        worker = self.registry_store.get(input.worker_id)
        self.registry_store.deregister(input.worker_id)
        if worker is not None:
            self._emit_catalog_changed(worker.worker_type, worker.spec_version)
        return DeregisterWorkerOutput(deregistered=True).__dict__

    def _emit_catalog_changed(self, worker_type: str, spec_version: str) -> None:
        if self.message_broker_client is None or not worker_type:
            return
        try:
            self.message_broker_client.publish_to_topic(
                CATALOG_CHANGED_TOPIC,
                {
                    "action": "deregistered",
                    "worker_type": worker_type,
                    "spec_version": spec_version,
                    "tenant_id": "system",
                },
            )
        except Exception as exc:
            self.logger.warning(
                "catalog.changed publish failed (deregister)",
                worker_type=worker_type,
                error=str(exc),
            )
