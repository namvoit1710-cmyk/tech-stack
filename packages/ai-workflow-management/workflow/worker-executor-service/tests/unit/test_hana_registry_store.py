import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.persistence.hana.hana_worker_registry_store import HanaWorkerRegistryStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability


class MockDB:
    def __init__(self):
        self.writes = []
        self._query_results = []

    def execute_write(self, sql, params=()):
        self.writes.append({"sql": sql, "params": params})

    def execute_query(self, sql, params=()):
        return self._query_results

    def set_query_results(self, results):
        self._query_results = results


def _make_worker_row(worker_id="w1", name="test-worker", caps=None, endpoint="http://localhost:9000"):
    return {
        "id": worker_id,
        "name": name,
        "capabilities": json.dumps(caps or [{"domain": "nlp", "action": "translate"}]),
        "endpoint_url": endpoint,
        "health_status": "healthy",
        "last_heartbeat": "2025-01-01T00:00:00Z",
        "ttl_seconds": 60,
    }


def test_register_worker():
    db = MockDB()
    store = HanaWorkerRegistryStore(db)
    worker = WorkerRegistration(
        id="w1",
        name="test-worker",
        capabilities=[WorkerCapability(domain="nlp", action="translate")],
        endpoint_url="http://localhost:9000",
    )
    store.register(worker)
    assert len(db.writes) == 1
    assert "UPSERT" in db.writes[0]["sql"]
    assert db.writes[0]["params"][0] == "w1"


def test_heartbeat_found():
    db = MockDB()
    db.set_query_results([{"1": 1}])
    store = HanaWorkerRegistryStore(db)
    result = store.heartbeat("w1")
    assert result is True
    assert len(db.writes) == 1
    assert "UPDATE" in db.writes[0]["sql"]
    assert db.writes[0]["params"][2] == "w1"


def test_heartbeat_not_found():
    db = MockDB()
    db.set_query_results([])
    store = HanaWorkerRegistryStore(db)
    result = store.heartbeat("nonexistent")
    assert result is False
    assert len(db.writes) == 0


def test_deregister():
    db = MockDB()
    store = HanaWorkerRegistryStore(db)
    store.deregister("w1")
    assert len(db.writes) == 1
    assert "DELETE" in db.writes[0]["sql"]


def test_get_worker_found():
    db = MockDB()
    db.set_query_results([_make_worker_row()])
    store = HanaWorkerRegistryStore(db)
    worker = store.get("w1")
    assert worker is not None
    assert worker.id == "w1"
    assert worker.name == "test-worker"
    assert len(worker.capabilities) == 1
    assert worker.capabilities[0].domain == "nlp"


def test_get_worker_not_found():
    db = MockDB()
    db.set_query_results([])
    store = HanaWorkerRegistryStore(db)
    worker = store.get("nonexistent")
    assert worker is None


def test_list_all():
    db = MockDB()
    db.set_query_results([_make_worker_row("w1"), _make_worker_row("w2", name="w2")])
    store = HanaWorkerRegistryStore(db)
    workers = store.list_all()
    assert len(workers) == 2


def test_discover_all_when_no_capability():
    db = MockDB()
    db.set_query_results([_make_worker_row("w1"), _make_worker_row("w2")])
    store = HanaWorkerRegistryStore(db)
    workers = store.discover("")
    assert len(workers) == 2


def test_discover_by_capability():
    db = MockDB()
    db.set_query_results([
        _make_worker_row("w1", caps=[{"domain": "nlp", "action": "translate"}]),
        _make_worker_row("w2", caps=[{"domain": "vision", "action": "detect"}]),
    ])
    store = HanaWorkerRegistryStore(db)
    workers = store.discover("nlp")
    assert len(workers) == 1
    assert workers[0].id == "w1"


def test_discover_no_match():
    db = MockDB()
    db.set_query_results([_make_worker_row("w1", caps=[{"domain": "vision", "action": "detect"}])])
    store = HanaWorkerRegistryStore(db)
    workers = store.discover("nlp")
    assert len(workers) == 0


def test_row_to_entity_with_list_capabilities():
    """Test _row_to_entity when capabilities is already a list (not a string)."""
    db = MockDB()
    db.set_query_results([{
        "id": "w1",
        "name": "test",
        "capabilities": [{"domain": "a", "action": "b"}],
        "endpoint_url": "",
        "health_status": "healthy",
        "last_heartbeat": "",
        "ttl_seconds": 60,
    }])
    store = HanaWorkerRegistryStore(db)
    workers = store.list_all()
    assert len(workers) == 1
    assert workers[0].capabilities[0].domain == "a"
