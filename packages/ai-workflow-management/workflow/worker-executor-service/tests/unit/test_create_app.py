import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient

from main import create_app
from bootstrap import build_app_container


def _make_client():
    container = build_app_container()
    app = create_app()
    app.state.container = container
    return TestClient(app)


def _unwrap(resp):
    """Unwrap the standard response envelope."""
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


def test_health_endpoint():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["status"] == "ok"


def test_execute_task_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/tasks/execute", json={"task_id": "t1"})
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["status"] == "COMPLETED"


def test_report_result_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/tasks/report", json={"task_id": "t1", "status": "COMPLETED"})
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["reported"] is True


def test_route_task_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/tasks/route", json={"task_id": "t1", "capability": "nonexistent"})
    assert resp.status_code in (200, 404)


def test_report_health_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/heartbeat/report", json={"worker_id": "w1"})
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["worker_id"] == "w1"


def test_start_heartbeat_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/heartbeat/start/w1")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["started"] is True


def test_register_worker_endpoint():
    client = _make_client()
    resp = client.post("/api/v1/workers", json={"name": "test-w"})
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["name"] == "test-w"


def test_list_workers_endpoint():
    client = _make_client()
    resp = client.get("/api/v1/workers")
    assert resp.status_code == 200
    _unwrap(resp)


def test_register_and_get_worker():
    client = _make_client()
    reg_data = _unwrap(client.post("/api/v1/workers", json={"name": "w1"}))
    resp = client.get(f"/api/v1/workers/{reg_data['id']}")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["name"] == "w1"


def test_register_and_deregister_worker():
    client = _make_client()
    reg_data = _unwrap(client.post("/api/v1/workers", json={"name": "w1"}))
    resp = client.delete(f"/api/v1/workers/{reg_data['id']}")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["deregistered"] is True


def test_discover_workers_endpoint():
    client = _make_client()
    resp = client.get("/api/v1/workers/discover/nlp")
    assert resp.status_code == 200
    _unwrap(resp)


def test_worker_heartbeat_endpoint():
    client = _make_client()
    reg_data = _unwrap(client.post("/api/v1/workers", json={"name": "w1"}))
    resp = client.post(f"/api/v1/workers/{reg_data['id']}/heartbeat")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data["acknowledged"] is True
