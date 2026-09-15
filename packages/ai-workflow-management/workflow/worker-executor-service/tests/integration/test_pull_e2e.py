"""B10 (SA-2035) — end-to-end pull round-trip over real HTTP, + push-untouched proof.

Drives the whole pull lifecycle through the wired container + FastAPI app:
register(pull) → dispatch(enqueue) → queue-stats → lease → renew → callback(complete),
asserting the state at each hop. Also proves a push worker never touches the pull store.
"""

import sys
import os

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _ok(resp, status=200):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


@pytest.fixture
def pull_client(monkeypatch):
    """A TestClient whose container has pull ENABLED (ready store wired everywhere)."""
    from app.layer4_frameworks.config.app_config import settings
    monkeypatch.setattr(settings, "PULL_DELIVERY_ENABLED", True, raising=False)
    from main import create_app
    from bootstrap import build_app_container
    container = build_app_container()
    app = create_app()
    app.state.container = container
    return TestClient(app)


def test_pull_full_round_trip(pull_client):
    c = pull_client

    # 1. register a PULL worker
    reg = _ok(c.post("/api/v1/workers/register",
                     json={"worker_type": "pw-e2e", "delivery_mode": "pull", "name": "pw-e2e"}))
    assert reg["worker_type"] == "pw-e2e"

    # 2. WCP dispatches a task → it is ENQUEUED (stored), not pushed
    disp = _ok(c.post("/api/v1/tasks/execute", json={
        "task_id": "e1", "run_id": "r1", "node_id": "n1", "worker_type": "pw-e2e",
        "tenant": "t-A", "run_generation": "g1", "idempotency_key": "idem-e1",
        "payload": {"k": "v"},
    }))
    assert disp["status"] == "ENQUEUED"

    # 3. queue-stats shows the leasable backlog (the KEDA signal)
    qs = _ok(c.get("/api/v1/workers/type/pw-e2e/queue-stats", headers={"X-Tenant-Id": "t-A"}))
    assert qs["admitted_depth"] == 1 and qs["leased"] == 0

    # 4. the worker leases it
    leased = _ok(c.post("/api/v1/workers/type/pw-e2e/tasks/lease?max=5&wait=0",
                        headers={"X-Tenant-Id": "t-A"}))
    assert len(leased) == 1
    task = leased[0]
    assert task["task_id"] == "e1" and task["payload"] == {"k": "v"}
    token = task["lease_token"]
    assert token

    # 5. queue-stats now shows it leased (not leasable)
    qs2 = _ok(c.get("/api/v1/workers/type/pw-e2e/queue-stats", headers={"X-Tenant-Id": "t-A"}))
    assert qs2["admitted_depth"] == 0 and qs2["leased"] == 1

    # 6. renew the lease (long task) → new deadline
    renew = _ok(c.post("/api/v1/tasks/e1/lease/renew", headers={"X-Lease-Token": token}))
    assert renew["deadline"]

    # 7. a FORGED callback is rejected (triple-binding CAS) → 409, task still leased
    bad = c.post("/api/v1/tasks/callback",
                 json={"task_id": "e1", "status": "success", "worker_type": "pw-e2e"},
                 headers={"X-Lease-Token": "forged", "X-Tenant-Id": "t-A"})
    assert bad.status_code == 409
    assert _ok(c.get("/api/v1/workers/type/pw-e2e/queue-stats", headers={"X-Tenant-Id": "t-A"}))["leased"] == 1

    # 8. the real callback completes it
    cb = _ok(c.post("/api/v1/tasks/callback",
                    json={"task_id": "e1", "status": "success", "worker_type": "pw-e2e",
                          "outputs": {"uri": "s3://result"}},
                    headers={"X-Lease-Token": token, "X-Tenant-Id": "t-A"}))
    assert cb["accepted"] is True

    # 9. drained
    qs3 = _ok(c.get("/api/v1/workers/type/pw-e2e/queue-stats", headers={"X-Tenant-Id": "t-A"}))
    assert qs3["admitted_depth"] == 0 and qs3["leased"] == 0

    # 10. a second (duplicate) callback for the completed task is rejected
    dup = c.post("/api/v1/tasks/callback",
                 json={"task_id": "e1", "status": "success", "worker_type": "pw-e2e"},
                 headers={"X-Lease-Token": token, "X-Tenant-Id": "t-A"})
    assert dup.status_code == 409


def test_purge_run_removes_pull_tasks(pull_client):
    c = pull_client
    _ok(c.post("/api/v1/workers/register", json={"worker_type": "pw-purge", "delivery_mode": "pull"}))
    _ok(c.post("/api/v1/tasks/execute", json={
        "task_id": "p1", "run_id": "run-x", "worker_type": "pw-purge", "tenant": "t-A"}))
    assert _ok(c.get("/api/v1/workers/type/pw-purge/queue-stats", headers={"X-Tenant-Id": "t-A"}))["admitted_depth"] == 1
    purge = _ok(c.post("/api/v1/runs/run-x/tasks/purge"))
    assert purge["purged"] == 1
    assert _ok(c.get("/api/v1/workers/type/pw-purge/queue-stats", headers={"X-Tenant-Id": "t-A"}))["admitted_depth"] == 0


def test_push_worker_never_touches_pull_store(pull_client):
    """Push-untouched proof at the HTTP layer: a PUSH worker's dispatch takes the push
    path and the pull ready-store stays empty for it (even with pull enabled)."""
    c = pull_client
    # register a push worker (default mode) with no endpoint → push placeholder path
    _ok(c.post("/api/v1/workers/register", json={"worker_type": "pw-push", "name": "pw-push"}))
    disp = _ok(c.post("/api/v1/tasks/execute", json={
        "task_id": "push1", "run_id": "r1", "worker_type": "pw-push", "tenant": "t-A"}))
    # NOT the pull path — a no-endpoint push worker hits the sync placeholder (COMPLETED)
    assert disp["status"] == "COMPLETED"
    assert disp["status"] != "ENQUEUED"
    qs = _ok(c.get("/api/v1/workers/type/pw-push/queue-stats", headers={"X-Tenant-Id": "t-A"}))
    assert qs["admitted_depth"] == 0 and qs["leased"] == 0   # pull store untouched
