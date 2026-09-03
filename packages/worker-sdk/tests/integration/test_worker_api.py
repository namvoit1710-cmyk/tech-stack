from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "worker_type" in data
    assert "version" in data
    assert "sdk_version" in data
    assert data["sdk_version"] == "1.0.0"


def test_ready_endpoint(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_info_endpoint(client):
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert "worker_type" in data
    assert "version" in data
    assert "sdk_version" in data
    assert data["sdk_version"] == "1.0.0"


def test_execute_endpoint(client):
    response = client.post(
        "/api/v1/execute",
        json={
            "task_id": "t-100",
            "action": "test",
            "inputs": {"key": "value"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "t-100"
    assert data["status"] in (TaskStatus.SUCCESS, TaskStatus.ERROR)
