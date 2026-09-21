from fastapi.testclient import TestClient

def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_info_returns_agent_type(client: TestClient):
    resp = client.get("/api/v1/info")
    assert resp.status_code == 200
    assert resp.json()["agent_type"] == "file"
