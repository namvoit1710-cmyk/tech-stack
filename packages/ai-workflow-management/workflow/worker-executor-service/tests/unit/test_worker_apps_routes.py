"""SA-2047 R14 (Task 10) — route-level guarantees for the two worker-APP
endpoints: GET /workers/apps and GET /workers/apps/{worker_app}/instances.

Mirrors test_replicas_routes.py's fixture pattern (SA-2053/R6) deliberately:
same `_client()` / `_unwrap()` shape, registering through the SAME shared
`_registry_store` the routes read from rather than a use-case-level fixture,
and asserting the STATUS CODE (not just the body) for the same reason that
file gives -- a body-only assertion passes under a 404 too, because the
client parses the error envelope just as happily.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient

from main import create_app
from bootstrap import build_app_container
from app.layer1_domain.entities.worker_registration import WorkerRegistration

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
APP = "smdg-ai-tenant-1-worker-jira-worker"


def _client():
    container = build_app_container()
    app = create_app()
    app.state.container = container
    return TestClient(app), container["_registry_store"]


def _unwrap(resp):
    body = resp.json()
    assert body["status"] == "ok", body
    return body["data"]


def _register(store, worker_type, instance_id="jira-worker_0", status="healthy"):
    store.register(WorkerRegistration(
        worker_type=worker_type, instance_id=instance_id, health_status=status,
        endpoint_url=JIRA, last_heartbeat="2026-07-28T12:00:00+00:00",
        version="1.0.0", sdk_version="0.9.0",
    ))


def test_lists_worker_apps():
    client, store = _client()
    _register(store, "jira_create_issue")
    _register(store, "jira_get_issue")
    resp = client.get("/api/v1/workers/apps")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert [r["worker_app"] for r in data] == [APP]


def test_lists_app_instances():
    client, store = _client()
    _register(store, "jira_create_issue")
    _register(store, "jira_get_issue")
    resp = client.get(f"/api/v1/workers/apps/{APP}/instances")
    assert resp.status_code == 200
    data = _unwrap(resp)
    assert data[0]["instance_id"] == "jira-worker_0"


def test_apps_route_is_not_swallowed_by_the_worker_id_route():
    """THE ROUTE-ORDER TRAP -- same shape as
    test_workers_replicas_returns_the_aggregate_not_a_worker_lookup below.
    FastAPI matches in registration order, and @router.get("/workers/{worker_id}")
    is a catch-all. Declared below it, GET /workers/apps becomes
    get_worker(worker_id="apps") -> a 404 that reads as "no such worker" rather
    than "no such route". The registry is deliberately EMPTY here: /workers/apps
    must be 200-with-[] on a cold registry (soft-state registry: no apps
    registered is a valid, not-found-worthy answer), so a 404 can only be
    explained by the catch-all having won -- there is no route-agnostic reason
    for this assertion to fail once list_worker_apps exists at all. Moving the
    route below the catch-all must make this test fail."""
    client, _store = _client()
    resp = client.get("/api/v1/workers/apps")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert _unwrap(resp) == []
