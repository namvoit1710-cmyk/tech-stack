import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import RegisterWorkerUseCase, RegisterWorkerInput
from app.layer2_application.features.deregister_worker.use_cases.deregister_worker_usecase import DeregisterWorkerUseCase, DeregisterWorkerInput
from app.layer2_application.features.list_workers.use_cases.list_workers_usecase import ListWorkersUseCase
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, message: str, *args, **kwargs): pass
    def error(self, message: str, *args, **kwargs): pass
    def warning(self, message: str, *args, **kwargs): pass
    def debug(self, message: str, *args, **kwargs): pass


def test_deregister():
    store = MemoryWorkerRegistryStore()
    logger = DummyLogger()
    register_uc = RegisterWorkerUseCase(logger=logger, registry_store=store)
    deregister_uc = DeregisterWorkerUseCase(logger=logger, registry_store=store)
    list_uc = ListWorkersUseCase(logger=logger, registry_store=store)

    reg = register_uc.execute(RegisterWorkerInput(name="w1"))
    result = deregister_uc.execute(DeregisterWorkerInput(worker_id=reg["id"]))
    assert result["deregistered"] is True
    workers = list_uc.execute()
    assert len(workers) == 0


class _FakeBroker:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message))

    def publish_to_queue(self, *a, **kw): pass
    def consume_from_queue(self, *a, **kw): return None
    def acknowledge(self, *a, **kw): pass
    def subscribe_to_topic(self, *a, **kw): pass


def test_deregister_emits_catalog_changed():
    from app.layer1_domain.entities.worker_registration import WorkerRegistration
    store = MemoryWorkerRegistryStore()
    # Seed a registered worker so get() returns it and deregister knows the type.
    worker = WorkerRegistration(
        worker_type="http-worker",
        spec_version="2.0.0",
        name="http",
    )
    store.register(worker)

    broker = _FakeBroker()
    uc = DeregisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=store,
        message_broker_client=broker,
    )
    uc.execute(DeregisterWorkerInput(worker_id=worker.id))

    assert len(broker.published) == 1
    topic, message = broker.published[0]
    assert topic == "catalog.changed"
    assert message["action"] == "deregistered"
    assert message["worker_type"] == "http-worker"
    assert message["spec_version"] == "2.0.0"
    assert message["tenant_id"] == "system"


def test_deregister_no_emit_when_worker_unknown():
    """If the worker_id isn't in the store, no event fires."""
    store = MemoryWorkerRegistryStore()
    broker = _FakeBroker()
    uc = DeregisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=store,
        message_broker_client=broker,
    )
    uc.execute(DeregisterWorkerInput(worker_id="never-registered"))
    assert broker.published == []


def test_deregister_no_emit_when_broker_none():
    from app.layer1_domain.entities.worker_registration import WorkerRegistration
    store = MemoryWorkerRegistryStore()
    worker = WorkerRegistration(worker_type="http-worker", name="http")
    store.register(worker)

    uc = DeregisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=store,
        message_broker_client=None,
    )
    # No broker wired → should just deregister without attempting to publish.
    uc.execute(DeregisterWorkerInput(worker_id=worker.id))
    assert len(store.list_all()) == 0
