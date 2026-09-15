import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer2_application.features.route_task.use_cases.route_task_usecase import RouteTaskUseCase, RouteTaskInput
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_route_task():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    route_uc = RouteTaskUseCase(logger=logger, registry_store=store)

    register_uc.execute(RegisterWorkerInput(
        name="w1",
        capabilities=[{"domain": "nlp", "action": "translate"}],
        endpoint_url="http://localhost:9001",
    ))
    result = route_uc.execute(RouteTaskInput(task_id="t1", capability="nlp"))
    assert result["task_id"] == "t1"
    assert result["selected_worker_endpoint"] == "http://localhost:9001"


def test_route_task_no_workers():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    route_uc = RouteTaskUseCase(logger=logger, registry_store=store)

    result = route_uc.execute(RouteTaskInput(task_id="t1", capability="nonexistent"))
    assert "error" in result
