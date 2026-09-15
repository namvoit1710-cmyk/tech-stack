"""Get worker schema use case."""

from dataclasses import dataclass
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


@dataclass
class GetWorkerSchemaInput:
    worker_id: str = ""


@dataclass
class GetWorkerSchemaOutput:
    worker_id: str = ""
    worker_type: str = ""
    version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None


class GetWorkerSchemaUseCase:
    """Retrieves the input/output schemas of a registered worker."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store

    def execute(self, input: GetWorkerSchemaInput) -> dict:
        self.logger.info("Getting worker schema", worker_id=input.worker_id)
        worker = self.registry_store.get(input.worker_id)
        if not worker:
            worker = self.registry_store.get_by_worker_type(input.worker_id)
        if not worker:
            return {"error": "Worker not found"}
        return GetWorkerSchemaOutput(
            worker_id=worker.id,
            worker_type=worker.worker_type,
            version=worker.version,
            input_schema=worker.input_schema,
            output_schema=worker.output_schema,
        ).__dict__
