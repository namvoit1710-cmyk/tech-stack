"""Unit tests for the R10 control-plane proxy routes:

- GET /api/v2/worker-replicas               (aggregate summary, proxies R6)
- GET /api/v2/worker-replicas/{worker_type} (per-instance detail, proxies R6)

The frontend (`workflow-management`'s `worker-replicas.ts`) types these
responses directly as ``T[]`` — ``apiV2.get<IWorkerReplicaAggregate[]>(...)``
— so both routes must return a BARE JSON array, not the ``{status, data}``
envelope ``ResponseWrapperMiddleware`` applies to the rest of ``/api/v2/*``.

TDD: this file fails to collect/pass until ``worker_replicas_controller.py``
exists and is registered into the v2 router.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app

SUMMARY_ROWS = [
    {
        "worker_type": "e2e-http",
        "delivery_mode": "push",
        "replicas_total": 2,
        "replicas_healthy": 2,
        "oldest_heartbeat_age_seconds": 3.5,
    },
    {
        "worker_type": "jira_search",
        "delivery_mode": "pull",
        "replicas_total": 1,
        "replicas_healthy": 0,
        "oldest_heartbeat_age_seconds": 900.0,
    },
]

TYPE_ROWS = [
    {
        "instance_id": "inst-1",
        "status": "healthy",
        "last_heartbeat": "2026-07-21T00:00:00Z",
        "age_seconds": 3.5,
        "host": "10.0.0.1",
        "version": "1.2.3",
        "sdk_version": "0.9.0",
        "started_at": "2026-07-20T23:00:00Z",
        "delivery_mode": "push",
    },
    {
        "instance_id": "inst-2",
        "status": "stale",
        "last_heartbeat": "2026-07-20T23:50:00Z",
        "age_seconds": 610.0,
        "host": "10.0.0.2",
        "version": "1.2.3",
        "sdk_version": "0.9.0",
        "started_at": "2026-07-20T22:00:00Z",
        "delivery_mode": "push",
    },
]


class _FakeRegistryClient:
    """Fake ``IWorkerRegistryClient`` — only the two methods this feature uses."""

    def __init__(self, summary_rows: Any, type_rows: Any) -> None:
        self._summary_rows = summary_rows
        self._type_rows = type_rows

    def get_replica_summary(self):
        return self._summary_rows

    def get_type_replicas(self, worker_type: str):
        if worker_type != "e2e-http":
            return []
        return self._type_rows


class _UnreachableRegistryClient:
    """Simulates a transport failure — both calls return None."""

    def get_replica_summary(self):
        return None

    def get_type_replicas(self, worker_type: str):
        return None


def _client(registry_client: Any) -> TestClient:
    container: dict[str, Any] = {"_worker_registry_client": registry_client}
    app = create_fastapi_app(container)
    return TestClient(app)


class TestWorkerReplicasAggregate:
    def test_returns_bare_array_not_envelope_wrapped(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, TYPE_ROWS)).get("/api/v2/worker-replicas")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list), f"expected a bare array, got {type(body).__name__}: {body!r}"
        assert body == SUMMARY_ROWS

    def test_transport_failure_returns_empty_array_not_500(self):
        r = _client(_UnreachableRegistryClient()).get("/api/v2/worker-replicas")
        assert r.status_code == 200
        assert r.json() == []


class TestWorkerTypeReplicas:
    def test_returns_bare_mapped_array(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, TYPE_ROWS)).get("/api/v2/worker-replicas/e2e-http")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list), f"expected a bare array, got {type(body).__name__}: {body!r}"
        assert len(body) == 2

        first = body[0]
        assert first["id"] == "inst-1"
        assert first["instance_id"] == "inst-1"
        assert first["health_status"] == "healthy"
        assert "status" not in first  # executor's `status` field renamed to `health_status`
        assert first["host"] == "10.0.0.1"
        assert first["last_heartbeat"] == "2026-07-21T00:00:00Z"
        assert first["age_seconds"] == 3.5
        assert first["version"] == "1.2.3"

    def test_unknown_type_returns_empty_array(self):
        r = _client(_FakeRegistryClient(SUMMARY_ROWS, TYPE_ROWS)).get("/api/v2/worker-replicas/unknown-type")
        assert r.status_code == 200
        assert r.json() == []

    def test_transport_failure_returns_empty_array_not_500(self):
        r = _client(_UnreachableRegistryClient()).get("/api/v2/worker-replicas/e2e-http")
        assert r.status_code == 200
        assert r.json() == []
