"""SA-2053 (R6) — route-level guarantees for the two replica endpoints.

These assert the STATUS CODE, not just the body: a body-only assertion passes
under a 404 too, because the client parses the error envelope just as happily.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient

from main import create_app
from bootstrap import build_app_container
from app.layer1_domain.entities.worker_registration import WorkerRegistration


def _client():
    container = build_app_container()
    app = create_app()
    app.state.container = container
    return TestClient(app), container["_registry_store"]


def _unwrap(resp):
    body = resp.json()
    assert body["status"] == "ok", body
    return body["data"]


def _register(store, instance_id, worker_type="jira_search", status="healthy"):
    store.register(WorkerRegistration(
        instance_id=instance_id, worker_type=worker_type, health_status=status,
        last_heartbeat="2026-07-21T12:00:00+00:00", host="10.0.0.1",
        version="1.0.0", sdk_version="0.9.0",
    ))


def test_type_replicas_returns_one_row_per_instance():
    client, store = _client()
    _register(store, "w_0")
    _register(store, "w_1")
    resp = client.get("/api/v1/workers/type/jira_search/replicas")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert sorted(r["instance_id"] for r in data) == ["w_0", "w_1"]


def test_unknown_type_is_200_with_an_empty_list_never_404():
    """An empty SET is a valid answer. 404 is correct for /workers/{id} — that
    addresses a ROW — and copying its JSONResponse block here is the trap."""
    client, _store = _client()
    resp = client.get("/api/v1/workers/type/does-not-exist/replicas")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert _unwrap(resp) == []


def test_workers_replicas_returns_the_aggregate_not_a_worker_lookup():
    """THE ROUTE-ORDER TRAP. FastAPI matches in registration order, and
    @router.get("/workers/{worker_id}") is a catch-all. Declared below it, this
    request becomes get_worker(worker_id="replicas") -> a 404 that reads as
    "no such worker" rather than "no such route". Moving the route below the
    catch-all must make this test fail."""
    client, store = _client()
    _register(store, "w_0")
    _register(store, "w_1")
    _register(store, "x_0", worker_type="slack_post")
    resp = client.get("/api/v1/workers/replicas")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    data = _unwrap(resp)
    assert isinstance(data, list)
    by_type = {r["worker_type"]: r for r in data}
    assert by_type["jira_search"]["replicas_total"] == 2
    assert by_type["slack_post"]["replicas_total"] == 1
    # A worker row would have "instance_id"/"endpoint_url"; an aggregate row does not.
    assert "replicas_healthy" in by_type["jira_search"]


def test_workers_replicas_is_empty_list_on_an_empty_registry():
    client, store = _client()
    store._store.clear()
    resp = client.get("/api/v1/workers/replicas")
    assert resp.status_code == 200
    assert _unwrap(resp) == []
