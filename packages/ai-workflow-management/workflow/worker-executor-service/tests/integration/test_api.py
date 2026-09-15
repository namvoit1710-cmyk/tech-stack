import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def assert_ok(response, status_code=200):
    assert response.status_code == status_code
    body = response.json()
    assert body["status"] == "ok"
    return body["data"]


def assert_error(response, code=None):
    body = response.json()
    assert body["status"] == "error"
    assert "code" in body["error"]
    assert "message" in body["error"]
    if code:
        assert body["error"]["code"] == code
    return body["error"]


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = assert_ok(response)
    assert data["status"] == "ok"


def test_execute_task_endpoint(client):
    response = client.post(
        "/api/v1/tasks/execute",
        json={"task_id": "t1", "run_id": "r1", "node_id": "n1"},
    )
    assert response.status_code == 200
    data = assert_ok(response)
    assert data["task_id"] == "t1"
    assert data["status"] == "COMPLETED"


def test_report_health_endpoint(client):
    response = client.post(
        "/api/v1/heartbeat/report",
        json={"worker_id": "w1", "healthy": True},
    )
    assert response.status_code == 200
    data = assert_ok(response)
    assert data["worker_id"] == "w1"


def test_register_worker_endpoint(client):
    response = client.post(
        "/api/v1/workers",
        json={
            "name": "test-worker",
            "capabilities": [{"domain": "test", "action": "run"}],
            "endpoint_url": "http://localhost:9000",
        },
    )
    assert response.status_code == 200
    data = assert_ok(response)
    assert data["name"] == "test-worker"


def test_list_workers_endpoint(client):
    response = client.get("/api/v1/workers")
    assert response.status_code == 200
    assert_ok(response)
