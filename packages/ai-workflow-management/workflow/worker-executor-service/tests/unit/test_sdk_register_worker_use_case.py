"""Tests for SdkRegisterWorkerUseCase — worker_type validation + payload accept."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
    SdkRegisterWorkerInput,
    SdkRegisterWorkerUseCase,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _make_uc() -> SdkRegisterWorkerUseCase:
    return SdkRegisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=MemoryWorkerRegistryStore(),
    )


class TestWorkerTypeValidation:
    def test_rejects_empty_worker_type(self):
        uc = _make_uc()
        with pytest.raises(ValueError, match="worker_type is required"):
            uc.execute(SdkRegisterWorkerInput(worker_type=""))

    def test_rejects_whitespace_only_worker_type(self):
        uc = _make_uc()
        with pytest.raises(ValueError, match="worker_type is required"):
            uc.execute(SdkRegisterWorkerInput(worker_type="   "))

    def test_accepts_valid_worker_type(self):
        uc = _make_uc()
        result = uc.execute(SdkRegisterWorkerInput(
            worker_type="my-worker",
            version="1.0.0",
            endpoint="http://worker:8000",
            sdk_version="0.1.0",
        ))
        assert result["worker_type"] == "my-worker"
        assert result["worker_id"]


class TestPayloadPassthrough:
    def test_full_payload_registers_cleanly(self):
        uc = _make_uc()
        result = uc.execute(SdkRegisterWorkerInput(
            worker_type="http-worker",
            version="1.2.3",
            endpoint="http://host:8080",
            sdk_version="0.2.0",
            name="HTTP",
            description="Calls HTTP endpoints",
            node_class="TECHNICAL",
            icon="Globe",
            color="#FF0000",
            tags=["io", "http"],
            capabilities=[{"domain": "http", "action": "call"}],
            ports={"in": [{"id": "in"}], "out": [{"id": "out"}]},
        ))
        assert result["worker_type"] == "http-worker"
        assert result["version"] == "1.2.3"
        assert result["endpoint_url"] == "http://host:8080"

    def test_defaults_capability_when_missing(self):
        uc = _make_uc()
        result = uc.execute(SdkRegisterWorkerInput(worker_type="x"))
        assert result["worker_type"] == "x"


class TestSpecVersion:
    def test_spec_version_defaults_to_one_zero_zero_when_missing(self):
        """Empty spec_version on input should be normalized to '1.0.0'."""
        from app.layer4_frameworks.repositories.memory_worker_registry_store import (
            MemoryWorkerRegistryStore,
        )
        store = MemoryWorkerRegistryStore()
        uc = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", spec_version=""))
        workers = store.list_all()
        assert len(workers) == 1
        assert workers[0].spec_version == "1.0.0"

    def test_spec_version_passthrough(self):
        from app.layer4_frameworks.repositories.memory_worker_registry_store import (
            MemoryWorkerRegistryStore,
        )
        store = MemoryWorkerRegistryStore()
        uc = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", spec_version="3.2.1"))
        workers = store.list_all()
        assert workers[0].spec_version == "3.2.1"


class _FakeBroker:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message))

    def publish_to_queue(self, *a, **kw): pass
    def consume_from_queue(self, *a, **kw): return None
    def acknowledge(self, *a, **kw): pass
    def subscribe_to_topic(self, *a, **kw): pass


class TestCatalogChangedEmission:
    def test_emits_catalog_changed_on_register(self):
        from app.layer4_frameworks.repositories.memory_worker_registry_store import (
            MemoryWorkerRegistryStore,
        )
        broker = _FakeBroker()
        uc = SdkRegisterWorkerUseCase(
            logger=DummyLogger(),
            registry_store=MemoryWorkerRegistryStore(),
            message_broker_client=broker,
        )
        uc.execute(SdkRegisterWorkerInput(worker_type="http", spec_version="2.1.0"))
        assert len(broker.published) == 1
        topic, message = broker.published[0]
        assert topic == "catalog.changed"
        assert message["action"] == "registered"
        assert message["worker_type"] == "http"
        assert message["spec_version"] == "2.1.0"
        assert message["tenant_id"] == "system"

    def test_no_emission_when_broker_is_none(self):
        from app.layer4_frameworks.repositories.memory_worker_registry_store import (
            MemoryWorkerRegistryStore,
        )
        uc = SdkRegisterWorkerUseCase(
            logger=DummyLogger(),
            registry_store=MemoryWorkerRegistryStore(),
            message_broker_client=None,
        )
        uc.execute(SdkRegisterWorkerInput(worker_type="http"))
        # No broker → no publish; the call just succeeds.

    def test_publish_failure_does_not_break_registration(self):
        from app.layer4_frameworks.repositories.memory_worker_registry_store import (
            MemoryWorkerRegistryStore,
        )

        class _ExplodingBroker:
            def publish_to_topic(self, *a, **kw):
                raise RuntimeError("broker down")
            def publish_to_queue(self, *a, **kw): pass
            def consume_from_queue(self, *a, **kw): return None
            def acknowledge(self, *a, **kw): pass
            def subscribe_to_topic(self, *a, **kw): pass

        store = MemoryWorkerRegistryStore()
        uc = SdkRegisterWorkerUseCase(
            logger=DummyLogger(),
            registry_store=store,
            message_broker_client=_ExplodingBroker(),
        )
        out = uc.execute(SdkRegisterWorkerInput(worker_type="http"))
        # Registration completes even though the event publish failed.
        assert out["worker_type"] == "http"
        assert len(store.list_all()) == 1
