from typing import Protocol

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus


class IWorkerRegistry(Protocol):
    async def register(self, registration: WorkerRegistration) -> str:
        """Register this worker with the registry. Returns worker_id."""
        ...

    async def heartbeat(self, worker_id: str, status: WorkerStatus) -> dict:
        """Send a heartbeat to the registry. Returns the response body."""
        ...

    async def deregister(self, worker_id: str) -> None:
        """Deregister this worker from the registry."""
        ...
