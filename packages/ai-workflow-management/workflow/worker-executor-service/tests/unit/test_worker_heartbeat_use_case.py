import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer2_application.features.worker_heartbeat.use_cases.worker_heartbeat_usecase import WorkerHeartbeatUseCase, WorkerHeartbeatInput
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_worker_heartbeat():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    heartbeat_uc = WorkerHeartbeatUseCase(logger=logger, registry_store=store)

    reg = register_uc.execute(RegisterWorkerInput(name="w1"))
    result = heartbeat_uc.execute(WorkerHeartbeatInput(worker_id=reg["id"]))
    assert result["acknowledged"] is True
    assert result["re_register"] is False


def test_worker_heartbeat_unknown_worker_signals_re_register():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    heartbeat_uc = WorkerHeartbeatUseCase(logger=logger, registry_store=store)

    result = heartbeat_uc.execute(WorkerHeartbeatInput(worker_id="nonexistent"))
    assert result["acknowledged"] is False
    assert result["re_register"] is True
