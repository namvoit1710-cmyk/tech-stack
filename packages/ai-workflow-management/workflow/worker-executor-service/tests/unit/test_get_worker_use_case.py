import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer2_application.features.get_worker.use_cases.get_worker_usecase import GetWorkerUseCase, GetWorkerInput
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_get_worker():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    get_uc = GetWorkerUseCase(logger=logger, registry_store=store)

    reg = register_uc.execute(RegisterWorkerInput(name="w1"))
    result = get_uc.execute(GetWorkerInput(worker_id=reg["id"]))
    assert result["name"] == "w1"


def test_get_worker_not_found():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    get_uc = GetWorkerUseCase(logger=logger, registry_store=store)

    result = get_uc.execute(GetWorkerInput(worker_id="nonexistent"))
    assert result["error"] == "Worker not found"
