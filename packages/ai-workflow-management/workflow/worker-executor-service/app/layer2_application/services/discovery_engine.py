"""Discovery engine for finding workers by capability."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
    from app.layer1_domain.entities.worker_registration import WorkerRegistration


class DiscoveryEngine:
    """Finds workers that match a given capability."""

    @staticmethod
    def find_workers(capability: str, registry: "IWorkerRegistryStore") -> list["WorkerRegistration"]:
        """Find all workers that can handle the specified capability.

        Args:
            capability: The capability domain or action to search for
            registry: The worker registry store to search in

        Returns:
            List of matching WorkerRegistration entities
        """
        return registry.discover(capability)
