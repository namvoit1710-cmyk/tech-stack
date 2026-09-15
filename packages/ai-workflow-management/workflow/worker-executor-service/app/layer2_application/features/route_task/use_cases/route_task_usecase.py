"""Route task use case."""

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.entities.task_route import TaskRoute
from app.layer2_application.services.discovery_engine import DiscoveryEngine
from app.layer2_application.services.load_balancer import LoadBalancer


@dataclass
class RouteTaskInput:
    task_id: str = ""
    capability: str = ""


@dataclass
class RouteTaskOutput:
    task_id: str = ""
    capability: str = ""
    selected_worker_id: str = ""
    selected_worker_endpoint: str = ""


class RouteTaskUseCase:
    """Routes a task to the best available worker."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: RouteTaskInput) -> dict:
        self.logger.info("Routing task", task_id=input.task_id, capability=input.capability)

        workers = DiscoveryEngine.find_workers(input.capability, self.registry_store)
        selected = LoadBalancer.select_worker(workers)

        if not selected:
            self.logger.warning("No workers available for capability", capability=input.capability)
            return {"error": "No workers available", "task_id": input.task_id, "capability": input.capability}

        route = TaskRoute(
            task_id=input.task_id,
            capability=input.capability,
            selected_worker_id=selected.id,
        )

        return RouteTaskOutput(
            task_id=route.task_id,
            capability=route.capability,
            selected_worker_id=route.selected_worker_id,
            selected_worker_endpoint=selected.endpoint_url,
        ).__dict__
