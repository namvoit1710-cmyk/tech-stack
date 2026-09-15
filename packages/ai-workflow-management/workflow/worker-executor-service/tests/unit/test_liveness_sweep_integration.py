"""SA-2051 (R4) — end-to-end: real MemoryWorkerRegistryStore + real
SweepStaleWorkersUseCase + a fake broker. The two spec §8 integration rows."""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)
from app.layer2_application.features.sweep_stale_workers.use_cases.sweep_stale_workers_usecase import (
    SweepStaleWorkersUseCase,
)


class _DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class _FakeBroker:
    def __init__(self):
        self.published = []

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message))


class _Config:
    WORKER_STALE_AFTER_SECONDS = 120
    WORKER_REMOVE_AFTER_SECONDS = 600


def _reg(instance_id, worker_type, hb_ago_seconds):
    hb = (datetime.now(timezone.utc) - timedelta(seconds=hb_ago_seconds)).isoformat()
    return WorkerRegistration(instance_id=instance_id, worker_type=worker_type,
                              last_heartbeat=hb)


def test_one_dead_among_live_does_not_emit_and_type_still_resolves():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("dead", "t", 200))
    store.register(_reg("live", "t", 10))
    broker = _FakeBroker()
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=broker, app_config=_Config(),
    )

    uc.execute()

    assert broker.published == []
    assert store.get_by_worker_type("t") is not None


def test_all_dead_emits_deregistered_once():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("d0", "t", 200))
    store.register(_reg("d1", "t", 200))
    broker = _FakeBroker()
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=broker, app_config=_Config(),
    )

    uc.execute()

    assert len(broker.published) == 1
    topic, message = broker.published[0]
    assert topic == "catalog.changed"
    assert message["action"] == "deregistered"
    assert message["worker_type"] == "t"
    assert message["tenant_id"] == "system"
