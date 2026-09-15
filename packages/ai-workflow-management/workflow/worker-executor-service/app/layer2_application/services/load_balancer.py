"""Load balancer for selecting workers."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.layer1_domain.entities.worker_registration import WorkerRegistration


class LoadBalancer:
    """Simple round-robin load balancer for worker selection."""

    _counter: int = 0

    @classmethod
    def select_worker(cls, workers: list["WorkerRegistration"]) -> "WorkerRegistration | None":
        """Select a worker from the list using round-robin.

        Args:
            workers: List of available workers

        Returns:
            Selected worker or None if list is empty
        """
        if not workers:
            return None
        selected = workers[cls._counter % len(workers)]
        cls._counter += 1
        return selected
