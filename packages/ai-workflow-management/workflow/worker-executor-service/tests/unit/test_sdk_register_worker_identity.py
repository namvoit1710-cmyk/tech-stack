"""R1 (SA-2048) — identity on the SDK register path.

The use case resolves NOTHING. It strips the raw instance_id and forwards it;
WorkerRegistration.__post_init__ derives the registration id from the
(instance_id, worker_type) pair. These tests pin what comes back on the wire.

R1 adds no round-trip: the SDK already reads `worker_id` off the register response
and replays it on heartbeat. And R1 is inert — no SDK sends an instance_id today,
so every live registration takes the "no process named" branch and behaves exactly
as it did before this ticket.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
    SdkRegisterWorkerInput,
    SdkRegisterWorkerUseCase,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _make_uc() -> SdkRegisterWorkerUseCase:
    return SdkRegisterWorkerUseCase(
        logger=DummyLogger(),
        registry_store=MemoryWorkerRegistryStore(),
    )


def _only_stored(uc: SdkRegisterWorkerUseCase):
    """The single registration currently in the store."""
    workers = uc.registry_store.list_all()
    assert len(workers) == 1
    return workers[0]


class TestIdentityOnTheWire:
    def test_no_instance_id_makes_the_registration_its_own_instance(self):
        """The live path — no SDK sends an instance_id (keystone §3.3/§4.2).

        The entity's uuid4 default stands and instance_id adopts it, so
        worker_id == instance_id and the SDK's heartbeat replay is unaffected.
        """
        uc = _make_uc()
        out = uc.execute(SdkRegisterWorkerInput(worker_type="http-request-worker"))
        assert out["worker_id"] != ""
        assert out["instance_id"] == out["worker_id"]
        assert ":" not in out["worker_id"]   # a bare uuid4, not a derived pair

    def test_supplied_instance_id_becomes_the_pair(self):
        """rev-2 returned worker_id == "pod-abc" — the PROCESS. A second
        worker_type from pod-abc would then have reused that same id."""
        uc = _make_uc()
        out = uc.execute(SdkRegisterWorkerInput(
            worker_type="http-request-worker",
            instance_id="pod-abc",
        ))
        assert out["worker_id"] == "pod-abc:http-request-worker"
        assert out["instance_id"] == "pod-abc"

    def test_two_types_from_one_process_are_two_registrations(self):
        """The jira-worker case through the use case (keystone §2.1).

        Scope note: this asserts the REGISTRATION ids the use case returns.
        SA-2049 (in this branch) keys the store by worker.id, so a jira-worker's
        types land on distinct rows — the store's own suite pins the 22-rows /
        66-rows counts. This test stays at the wire layer.
        """
        uc = _make_uc()
        a = uc.execute(SdkRegisterWorkerInput(
            worker_type="jira_create_issue", instance_id="jira-worker_0"))
        b = uc.execute(SdkRegisterWorkerInput(
            worker_type="jira_search_users", instance_id="jira-worker_0"))
        assert a["worker_id"] == "jira-worker_0:jira_create_issue"
        assert b["worker_id"] == "jira-worker_0:jira_search_users"
        assert a["instance_id"] == b["instance_id"] == "jira-worker_0"

    def test_whitespace_instance_id_is_treated_as_not_supplied(self):
        """"   " must never become the id "   :http-request-worker". The use case
        strips before the entity ever sees it."""
        uc = _make_uc()
        out = uc.execute(SdkRegisterWorkerInput(
            worker_type="http-request-worker",
            instance_id="   ",
        ))
        assert out["instance_id"].strip() != ""
        assert out["instance_id"] != "   "
        assert ":" not in out["worker_id"]
        assert out["instance_id"] == out["worker_id"]

    def test_synthesized_ids_differ_per_registration(self):
        uc = _make_uc()
        a = uc.execute(SdkRegisterWorkerInput(worker_type="http-request-worker"))
        b = uc.execute(SdkRegisterWorkerInput(worker_type="ms-teams-worker"))
        assert a["worker_id"] != b["worker_id"]

    def test_started_at_defaults_to_an_iso_timestamp(self):
        uc = _make_uc()
        uc.execute(SdkRegisterWorkerInput(worker_type="http-request-worker"))
        stored = _only_stored(uc)
        assert stored.started_at != ""
        assert "T" in stored.started_at

    def test_host_and_pid_round_trip_to_the_store(self):
        uc = _make_uc()
        uc.execute(SdkRegisterWorkerInput(
            worker_type="http-request-worker",
            instance_id="pod-abc",
            host="worker-http-request-worker-2",
            pid=4711,
            started_at="2026-07-17T09:00:00+00:00",
        ))
        stored = _only_stored(uc)
        assert stored.instance_id == "pod-abc"
        assert stored.host == "worker-http-request-worker-2"
        assert stored.pid == 4711
        assert stored.started_at == "2026-07-17T09:00:00+00:00"


class TestHttpRoundTrip:
    def _client(self):
        from fastapi.testclient import TestClient
        from main import create_app
        from bootstrap import build_app_container

        app = create_app()
        # create_app() does not wire app.state.container itself (see main.py
        # __main__ block / tests/unit/test_create_app.py::_make_client) — the
        # DI container is attached externally before the app serves requests.
        app.state.container = build_app_container()
        return TestClient(app)

    def test_register_returns_the_pair_over_http(self):
        """The pair survives pydantic -> model_dump() -> dataclass -> entity."""
        client = self._client()
        resp = client.post("/api/v1/workers/register", json={
            "worker_type": "http-request-worker",
            "instance_id": "pod-abc",
            "host": "worker-http-request-worker-2",
            "pid": 4711,
            "started_at": "2026-07-17T09:00:00+00:00",
        })
        assert resp.status_code == 200
        data = resp.json()
        data = data.get("data", data)  # ResponseWrapperMiddleware envelope
        assert data["worker_id"] == "pod-abc:http-request-worker"
        assert data["instance_id"] == "pod-abc"

    def test_old_sdk_register_then_heartbeat_acknowledged(self):
        """AC: no instance_id sent; the returned id must work on heartbeat.

        Proves the design call: because worker_id == instance_id, the existing
        path-param route matches unchanged and heartbeat_controller.py needs no edit.
        """
        client = self._client()
        resp = client.post("/api/v1/workers/register", json={
            "worker_type": "http-request-worker",
        })
        assert resp.status_code == 200
        data = resp.json()
        data = data.get("data", data)
        wid = data["worker_id"]
        assert wid != ""

        hb = client.post(f"/api/v1/workers/{wid}/heartbeat", json={"status": "healthy"})
        assert hb.status_code == 200
        hb_data = hb.json()
        hb_data = hb_data.get("data", hb_data)
        assert hb_data["acknowledged"] is True
