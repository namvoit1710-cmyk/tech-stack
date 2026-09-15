import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def _make_uc():
    return RegisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=MemoryWorkerRegistryStore(),
    )


def test_register_worker():
    uc = _make_uc()
    result = uc.execute(RegisterWorkerInput(
        name="test-worker",
        capabilities=[{"domain": "test", "action": "run"}],
        endpoint_url="http://localhost:9000",
    ))
    assert result["name"] == "test-worker"
    assert result["endpoint_url"] == "http://localhost:9000"
    assert len(result["capabilities"]) == 1
