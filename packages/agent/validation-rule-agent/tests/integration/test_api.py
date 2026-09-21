from fastapi.testclient import TestClient

def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_info_returns_agent_type(client: TestClient):
    resp = client.get("/api/v1/info")
    assert resp.status_code == 200
    # Corrected (SA-1444): deployment + env.example set AGENT_TYPE=validation-rule.
    # The prior "modify-file" value was stale from the modify-file-agent fork.
    assert resp.json()["agent_type"] == "validation-rule"


def test_dedicated_flag_endpoints_registered(client: TestClient):
    # SA-1444: the two flag-driven flows are exposed as dedicated, documented endpoints.
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/generate-polar" in paths
    assert "/api/v1/validate" in paths
    assert "/api/v1/validate-by-rules" in paths
