"""SA-2056 (R9) — get_replica_summary must distinguish transport failure (None)
from a genuine empty registry ([]). Copying list_workers' None->[] collapse is the
risk-review defect: a network flap would then tombstone every worker_type."""
from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient


def _client_with_get(get_impl):
    c = HttpWorkerRegistryClient(worker_execution_url="http://x")
    c._get = get_impl          # stub the shared GET helper
    return c


def test_unreachable_returns_none_not_empty_list():
    """_get returns None on any transport error. get_replica_summary must PASS THAT
    THROUGH — None means 'unknown', distinct from a real empty registry."""
    c = _client_with_get(lambda path: None)
    assert c.get_replica_summary() is None


def test_genuine_empty_200_returns_empty_list():
    c = _client_with_get(lambda path: [])
    assert c.get_replica_summary() == []


def test_populated_returns_the_rows():
    rows = [{"worker_type": "jira_search", "delivery_mode": "push",
             "replicas_total": 3, "replicas_healthy": 3, "oldest_heartbeat_age_seconds": 4.0}]
    c = _client_with_get(lambda path: rows)
    assert c.get_replica_summary() == rows


def test_calls_the_replicas_path():
    seen = {}
    def _get(path):
        seen["path"] = path
        return []
    _client_with_get(_get).get_replica_summary()
    assert seen["path"] == "/workers/replicas"
