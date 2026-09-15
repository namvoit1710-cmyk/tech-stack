import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer2_application.features.discover_workers.use_cases.discover_workers_usecase import DiscoverWorkersUseCase, DiscoverWorkersInput
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_discover_workers():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    discover_uc = DiscoverWorkersUseCase(logger=logger, registry_store=store)

    register_uc.execute(RegisterWorkerInput(
        name="w1",
        capabilities=[{"domain": "nlp", "action": "translate"}],
    ))
    register_uc.execute(RegisterWorkerInput(
        name="w2",
        capabilities=[{"domain": "vision", "action": "detect"}],
    ))
    result = discover_uc.execute(DiscoverWorkersInput(capability="nlp"))
    assert len(result) == 1
    assert result[0]["name"] == "w1"
