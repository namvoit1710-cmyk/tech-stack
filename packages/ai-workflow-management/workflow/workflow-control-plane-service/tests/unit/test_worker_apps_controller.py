"""Unit tests for the R14 control-plane proxy routes:

- GET /api/v2/worker-apps                        (per-app aggregate, proxies R14)
- GET /api/v2/worker-apps/{worker_app}/instances (per-instance detail, proxies R14)

The frontend types these responses directly as ``T[]`` — same contract as
``worker-replicas.ts`` (SA-2047 R10) — so both routes must return a BARE JSON
array, not the ``{status, data}`` envelope ``ResponseWrapperMiddleware``
applies to the rest of ``/api/v1|v2/*``.

Task 13's ``get_app_summary``/``get_app_instances`` collapse ``None`` (executor
unreachable, or a non-list body) and ``[]`` (genuinely empty) into the same
``[]`` HTTP response here — that tri-state distinction matters to the R9
realtime feed (Task 15), not to a snapshot HTTP read.

TDD: this file fails to collect/pass until ``worker_apps_controller.py``
exists, is registered into the v2 router, AND ``/api/v2/worker-apps`` is
carved out of ``ResponseWrapperMiddleware``'s ``ignore_paths`` — the bare-array
assertions below fail on envelope shape alone if that carve-out is missing,
independent of whether the controller/router wiring is otherwise correct.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app

SUMMARY_ROWS = [
    {
        "worker_app": "scout-mdg",
        "instances_total": 2,
        "instances_healthy": 2,
        "oldest_heartbeat_age_seconds": 3.5,
    },
    {
        "worker_app": "jira",
        "instances_total": 1,
        "instances_healthy": 0,
        "oldest_heartbeat_age_seconds": 900.0,
    },
]

INSTANCE_ROWS = [
    {
        "instance_id": "inst-1",
        "status": "healthy",
        "last_heartbeat": "2026-07-21T00:00:00Z",
        "age_seconds": 3.5,
        "host": "10.0.0.1",
        "version": "1.2.3",
    },
    {
        "instance_id": "inst-2",
        "status": "stale",
        "last_heartbeat": "2026-07-20T23:50:00Z",
        "age_seconds": 610.0,
        "host": "10.0.0.2",
        "version": "1.2.3",
    },
]


class _FakeRegistryClient:
    """Fake ``IWorkerRegistryClient`` — only the two methods this feature uses."""

    def __init__(self, summary_rows: Any, instance_rows: Any) -> None:
        self._summary_rows = summary_rows
        self._instance_rows = instance_rows

    def get_app_summary(self):
        return self._summary_rows

    def get_app_instances(self, worker_app: str):
        if worker_app != "scout-mdg":
            return []
        return self._instance_rows


class _UnreachableRegistryClient:
    """Simulates a transport failure (or a non-list body) — both calls return None."""

    def get_app_summary(self):
        return None

    def get_app_instances(self, worker_app: str):
        return None


def _client(registry_client: Any) -> TestClient:
    container: dict[str, Any] = {"_worker_registry_client": registry_client}
    app = create_fastapi_app(container)
    return TestClient(app)


class TestWorkerAppsSummary:
    def test_returns_bare_array_not_envelope_wrapped(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, INSTANCE_ROWS)).get("/api/v2/worker-apps")
        assert r.status_code == 200
        body = r.json()
        # Discriminates the ignore_paths carve-out: if /api/v2/worker-apps
        # were still wrapped, `body` would be {"status": "ok", "data": [...]}
        # (a dict), so `isinstance(body, list)` fails and `body == SUMMARY_ROWS`
        # fails too (dict != list) -- both assertions catch a missing carve-out
        # on their own, independent of whatever the controller returns.
        assert isinstance(body, list), f"expected a bare array, got {type(body).__name__}: {body!r}"
        assert body == SUMMARY_ROWS

    def test_rows_pass_through_unchanged(self):
        # Unlike worker-replicas' per-instance route, worker-apps summary rows
        # are NOT remapped -- assert field-for-field equality (not just length)
        # so a silent reshape/drop of a field would fail this test.
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, INSTANCE_ROWS)).get("/api/v2/worker-apps")
        assert r.json() == SUMMARY_ROWS

    def test_none_client_returns_empty_array(self):
        r = _client(None).get("/api/v2/worker-apps")
        assert r.status_code == 200
        assert r.json() == []

    def test_transport_failure_returns_empty_array_not_500(self):
        r = _client(_UnreachableRegistryClient()).get("/api/v2/worker-apps")
        assert r.status_code == 200
        assert r.json() == []


class TestWorkerAppInstances:
    def test_returns_bare_array_not_envelope_wrapped(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, INSTANCE_ROWS)).get(
            "/api/v2/worker-apps/scout-mdg/instances"
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list), f"expected a bare array, got {type(body).__name__}: {body!r}"
        assert body == INSTANCE_ROWS

    def test_rows_pass_through_unchanged(self):
        # worker-apps instances are a straight passthrough (no _to_worker_instance-
        # style remap like worker-replicas' per-type route) -- pin field names as
        # the executor sent them (`status`, not `health_status`).
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, INSTANCE_ROWS)).get(
            "/api/v2/worker-apps/scout-mdg/instances"
        )
        body = r.json()
        assert body == INSTANCE_ROWS
        assert body[0]["status"] == "healthy"

    def test_unknown_app_returns_empty_array(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, INSTANCE_ROWS)).get(
            "/api/v2/worker-apps/unknown-app/instances"
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_none_client_returns_empty_array(self):
        r = _client(None).get("/api/v2/worker-apps/scout-mdg/instances")
        assert r.status_code == 200
        assert r.json() == []

    def test_transport_failure_returns_empty_array_not_500(self):
        r = _client(_UnreachableRegistryClient()).get("/api/v2/worker-apps/scout-mdg/instances")
        assert r.status_code == 200
        assert r.json() == []
