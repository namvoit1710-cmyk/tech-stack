"""Tests for worker_type-based routing and re-registration.

Covers:
- SA-2049: worker_type is not identity -- distinct instances of one worker_type
  each keep their own row (the pre-R2 upsert-by-worker_type collapsed them)
- WorkerDispatcher resolves by worker_type when UUID lookup fails
- GetWorkerUseCase falls back to worker_type
- MemoryWorkerRegistryStore.get_by_worker_type
"""

import pytest

from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer4_frameworks.providers.worker_dispatcher import WorkerDispatcher


class TestMemoryRegistryWorkerType:

    def test_get_by_worker_type(self):
        store = MemoryWorkerRegistryStore()
        worker = WorkerRegistration(
            id="uuid-1", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://localhost:9000",
        )
        store.register(worker)
        found = store.get_by_worker_type("http-request-worker")
        assert found is not None
        assert found.id == "uuid-1"
        assert found.worker_type == "http-request-worker"

    def test_get_by_worker_type_not_found(self):
        store = MemoryWorkerRegistryStore()
        assert store.get_by_worker_type("nonexistent") is None

    def test_get_by_worker_type_empty_string(self):
        store = MemoryWorkerRegistryStore()
        assert store.get_by_worker_type("") is None

    def test_re_register_with_same_worker_type_keeps_both_instances(self):
        """SA-2049: two DISTINCT instances of one worker_type are two rows, not one.

        Pre-R2 this test was named test_re_register_reuses_id and asserted the
        replica-collapse bug directly: a second registration with the same
        worker_type reused the first instance's id and overwrote its row, so two
        live replicas of one worker_type became one shared entry (and desynced
        worker2.id from worker2.instance_id in the process).
        """
        store = MemoryWorkerRegistryStore()

        # First instance
        worker1 = WorkerRegistration(
            id="uuid-first", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://host1:9000",
        )
        store.register(worker1)
        assert store.get("uuid-first") is not None

        # A second, distinct instance of the SAME worker_type
        worker2 = WorkerRegistration(
            id="uuid-second", name="HTTP Worker v2",
            worker_type="http-request-worker",
            endpoint_url="http://host2:9000",
        )
        store.register(worker2)

        # Each instance keeps its own identity -- no id got reused/overwritten.
        assert worker1.id == "uuid-first"
        assert worker2.id == "uuid-second"
        assert store.get("uuid-first") is not None
        assert store.get("uuid-first").endpoint_url == "http://host1:9000"
        assert store.get("uuid-second") is not None
        assert store.get("uuid-second").endpoint_url == "http://host2:9000"
        # Both replicas of the same worker_type coexist.
        assert len(store.list_all()) == 2

    def test_register_without_worker_type_creates_new(self):
        """Workers without worker_type should always get new registrations."""
        store = MemoryWorkerRegistryStore()

        worker1 = WorkerRegistration(id="uuid-1", name="Worker 1", endpoint_url="http://h1")
        worker2 = WorkerRegistration(id="uuid-2", name="Worker 2", endpoint_url="http://h2")
        store.register(worker1)
        store.register(worker2)

        assert len(store.list_all()) == 2


class TestWorkerDispatcherWorkerType:

    def test_resolve_by_worker_type_when_uuid_missing(self):
        """Dispatcher should find worker by worker_type when UUID lookup fails."""
        store = MemoryWorkerRegistryStore()
        worker = WorkerRegistration(
            id="some-uuid", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://localhost:9000",
        )
        store.register(worker)

        dispatcher = WorkerDispatcher(registry_store=store)
        # Look up by worker_type (not UUID)
        executor = dispatcher.get_executor("http-request-worker")
        assert executor is not None

    def test_resolve_by_uuid_still_works(self):
        """UUID-based lookup should still work."""
        store = MemoryWorkerRegistryStore()
        worker = WorkerRegistration(
            id="some-uuid", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://localhost:9000",
        )
        store.register(worker)

        dispatcher = WorkerDispatcher(registry_store=store)
        executor = dispatcher.get_executor("some-uuid")
        assert executor is not None

    def test_resolve_after_re_registration(self):
        """After a worker restarts and re-registers, dispatching by worker_type still works."""
        store = MemoryWorkerRegistryStore()

        # First registration
        worker1 = WorkerRegistration(
            id="uuid-old", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://host1:9000",
        )
        store.register(worker1)

        # Worker restarts and re-registers
        worker2 = WorkerRegistration(
            id="uuid-new", name="HTTP Worker",
            worker_type="http-request-worker",
            endpoint_url="http://host2:9000",
        )
        store.register(worker2)

        dispatcher = WorkerDispatcher(registry_store=store)

        # Looking up by worker_type should find the updated worker
        executor = dispatcher.get_executor("http-request-worker")
        assert executor is not None

        # The old id is NOT overwritten. Keyed by worker.id (SA-2049), worker2
        # ("uuid-new") takes its own row and worker1's ("uuid-old") survives as an
        # ORPHAN still pointing at the now-dead host1. So a by-id lookup resolves —
        # to a dead endpoint — until SA-2051's sweeper reaps stale rows. Pre-SA-2049
        # the upsert recycled the id onto the live row; this test documents the
        # trade. Production dispatches by worker_type (above), never by a bare id.
        executor_by_id = dispatcher.get_executor("uuid-old")
        assert executor_by_id is not None

    def test_resolve_nonexistent_returns_none(self):
        store = MemoryWorkerRegistryStore()
        dispatcher = WorkerDispatcher(registry_store=store)
        assert dispatcher.get_executor("nonexistent") is None
