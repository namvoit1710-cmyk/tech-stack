import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.providers.worker_dispatcher import WorkerDispatcher
from app.layer4_frameworks.providers.http_worker_executor import HttpWorkerExecutor
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_register_and_get_static_executor():
    dispatcher = WorkerDispatcher()
    executor = HttpWorkerExecutor("http://localhost:9000")
    dispatcher.register_worker("w1", executor)
    result = dispatcher.get_executor("w1")
    assert result is executor


def test_get_executor_not_found():
    dispatcher = WorkerDispatcher()
    result = dispatcher.get_executor("nonexistent")
    assert result is None


def test_get_executor_from_registry_store():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    reg = register_uc.execute(RegisterWorkerInput(
        name="dynamic-worker",
        endpoint_url="http://localhost:9999",
    ))
    dispatcher = WorkerDispatcher(registry_store=store)
    executor = dispatcher.get_executor(reg["id"])
    assert executor is not None
    assert executor._worker_url == "http://localhost:9999"


def test_get_executor_from_registry_no_endpoint():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    reg = register_uc.execute(RegisterWorkerInput(name="no-endpoint"))
    dispatcher = WorkerDispatcher(registry_store=store)
    executor = dispatcher.get_executor(reg["id"])
    # worker exists but has no endpoint_url
    assert executor is None


def test_get_executor_by_capability():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    register_uc.execute(RegisterWorkerInput(
        name="nlp-worker",
        capabilities=[{"domain": "nlp", "action": "translate"}],
        endpoint_url="http://localhost:9001",
    ))
    dispatcher = WorkerDispatcher(registry_store=store)
    executor = dispatcher.get_executor_by_capability("nlp")
    assert executor is not None


def test_get_executor_by_capability_no_store():
    dispatcher = WorkerDispatcher()
    result = dispatcher.get_executor_by_capability("nlp")
    assert result is None


def test_get_executor_by_capability_no_match():
    store = MemoryWorkerRegistryStore()
    dispatcher = WorkerDispatcher(registry_store=store)
    result = dispatcher.get_executor_by_capability("nonexistent")
    assert result is None
