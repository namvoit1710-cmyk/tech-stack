"""SA-2047 (R14) — get_app_summary and get_app_instances must distinguish transport
failure (None) from a genuine empty registry ([]). Same risk as R9's replica reads:
collapsing None->[] would make a network flap during an outage read as "no worker
apps" / "no instances", i.e. a silently empty fleet instead of an unknown one."""
from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient


def _client_with_get(get_impl):
    c = HttpWorkerRegistryClient(worker_execution_url="http://x")
    c._get = get_impl          # stub the shared GET helper
    return c


# --- get_app_summary --------------------------------------------------------

def test_app_summary_unreachable_returns_none_not_empty_list():
    """_get returns None on any transport error. get_app_summary must PASS THAT
    THROUGH — None means 'unknown', distinct from a real empty registry."""
    c = _client_with_get(lambda path: None)
    assert c.get_app_summary() is None


def test_app_summary_genuine_empty_200_returns_empty_list():
    c = _client_with_get(lambda path: [])
    assert c.get_app_summary() == []


def test_app_summary_populated_returns_the_rows():
    rows = [{"worker_app": "jira-worker", "instances_total": 3, "instances_healthy": 3}]
    c = _client_with_get(lambda path: rows)
    assert c.get_app_summary() == rows


def test_app_summary_non_list_body_returns_none_and_logs(caplog):
    """A dict (or any non-list) body is a contract violation on this route —
    fail soft to None (not [] — that would read as a real empty registry, and
    not a crash), and log loudly so the violation is visible in R14 diagnostics."""
    c = _client_with_get(lambda path: {"unexpected": "shape"})
    with caplog.at_level("WARNING"):
        result = c.get_app_summary()
    assert result is None
    assert "non-list body" in caplog.text
    assert "/workers/apps" in caplog.text


def test_app_summary_calls_the_apps_path():
    seen = {}
    def _get(path):
        seen["path"] = path
        return []
    _client_with_get(_get).get_app_summary()
    assert seen["path"] == "/workers/apps"


# --- get_app_instances -------------------------------------------------------

def test_app_instances_unreachable_returns_none_not_empty_list():
    c = _client_with_get(lambda path: None)
    assert c.get_app_instances("jira-worker") is None


def test_app_instances_genuine_empty_200_returns_empty_list():
    c = _client_with_get(lambda path: [])
    assert c.get_app_instances("jira-worker") == []


def test_app_instances_populated_returns_the_rows():
    rows = [{"instance_id": "jira-worker-1", "status": "healthy"}]
    c = _client_with_get(lambda path: rows)
    assert c.get_app_instances("jira-worker") == rows


def test_app_instances_non_list_body_returns_none_and_logs(caplog):
    c = _client_with_get(lambda path: {"unexpected": "shape"})
    with caplog.at_level("WARNING"):
        result = c.get_app_instances("jira-worker")
    assert result is None
    assert "non-list body" in caplog.text
    assert "/workers/apps/jira-worker/instances" in caplog.text


def test_app_instances_calls_the_instances_path_for_the_given_app():
    seen = {}
    def _get(path):
        seen["path"] = path
        return []
    _client_with_get(_get).get_app_instances("jira-worker")
    assert seen["path"] == "/workers/apps/jira-worker/instances"
