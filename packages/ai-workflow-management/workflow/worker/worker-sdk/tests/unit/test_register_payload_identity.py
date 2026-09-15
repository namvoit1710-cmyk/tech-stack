"""R8 (SA-2055) §5 — identity on the SDK's own entity and on the register payload."""

import json

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration


class TestEntityFields:
    def test_defaults_are_blank_safe(self):
        """An embedder that sets none of these still registers — the registry
        fills them from the process (Task 5)."""
        w = WorkerRegistration(worker_type="w", version="1", endpoint="http://x")
        assert w.instance_id == ""
        assert w.host == ""
        assert w.pid == 0
        assert w.started_at == ""

    def test_fields_round_trip(self):
        w = WorkerRegistration(
            worker_type="w", version="1", endpoint="http://x",
            instance_id="jira-worker_0",
            host="10.0.1.23",
            pid=4711,
            started_at="2026-07-17T09:00:00+00:00",
        )
        assert w.instance_id == "jira-worker_0"
        assert w.host == "10.0.1.23"
        assert w.pid == 4711
        assert w.started_at == "2026-07-17T09:00:00+00:00"


class TestEntityWorkerApp:
    """R14 — the CF application_name, entity half. Same blank-safe/settable
    shape as instance_id/host/pid/started_at above."""

    def test_default_is_blank_safe(self):
        w = WorkerRegistration(worker_type="w", version="1", endpoint="http://x")
        assert w.worker_app == ""

    def test_field_round_trips(self):
        w = WorkerRegistration(
            worker_type="w", version="1", endpoint="http://x", worker_app="jira-worker",
        )
        assert w.worker_app == "jira-worker"


import os
import socket

import pytest

from worker_sdk.layer4_frameworks.providers.registry import http_worker_registry as mod


class _FakeResp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"data": {"worker_id": "w1"}}


class _CapturingClient:
    """Mirrors the shape test_worker_registry_kind.py:131 already uses."""
    def __init__(self):
        self.payloads = []
    async def post(self, url, json):
        self.payloads.append(json)
        return _FakeResp()
    async def aclose(self): pass


def _registry_with_capture():
    r = mod.HttpWorkerRegistry()
    client = _CapturingClient()
    r._client = client
    return r, client


def _clear_cf(monkeypatch):
    for var in ("VCAP_APPLICATION", "CF_INSTANCE_INDEX", "CF_INSTANCE_IP"):
        monkeypatch.delenv(var, raising=False)


class TestPayloadCarriesIdentity:
    @pytest.mark.asyncio
    async def test_payload_carries_cf_identity(self, monkeypatch):
        """§9 checkbox 1, at the wire."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        monkeypatch.setenv("CF_INSTANCE_IP", "10.0.1.23")
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w", version="1", endpoint="http://x"))
        body = client.payloads[0]
        assert body["instance_id"] == "jira-worker_0"
        assert body["host"] == "10.0.1.23"
        assert body["pid"] == os.getpid()
        assert body["started_at"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_payload_off_cf_sends_blank_instance_id(self, monkeypatch):
        """§9 checkbox 2 — blank, but host/pid/started_at are still useful."""
        _clear_cf(monkeypatch)
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w", version="1", endpoint="http://x"))
        body = client.payloads[0]
        assert body["instance_id"] == ""
        assert body["host"] == socket.gethostname()
        assert body["pid"] == os.getpid()
        assert body["started_at"] != ""

    @pytest.mark.asyncio
    async def test_entity_values_win_over_the_process(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(
            worker_type="w", version="1", endpoint="http://x",
            instance_id="pinned_9", host="h", pid=1234, started_at="2020-01-01T00:00:00+00:00",
        ))
        body = client.payloads[0]
        assert body["instance_id"] == "pinned_9"
        assert body["host"] == "h"
        assert body["pid"] == 1234
        assert body["started_at"] == "2020-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_the_18_existing_keys_are_untouched(self, monkeypatch):
        """R8 was additive (18 -> 22 keys); R14 is additive again (22 -> 23,
        worker_app). None of the original 18 move."""
        _clear_cf(monkeypatch)
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w", version="1", endpoint="http://x"))
        body = client.payloads[0]
        for key in ("worker_type", "version", "spec_version", "endpoint", "sdk_version",
                    "input_schema", "output_schema", "name", "description", "node_class",
                    "kind", "delivery_mode", "icon", "color", "tags", "capabilities",
                    "ports", "functions"):
            assert key in body, key
        assert len(body) == 23


class TestPayloadCarriesWorkerApp:
    """R14 — the register payload's ``worker_app`` line. The executor (Task 11)
    already accepts and falls back to deriving this from the endpoint host when
    absent; sending the real CF application_name closes that gap at the source."""

    @pytest.mark.asyncio
    async def test_payload_carries_worker_app_from_the_process(self, monkeypatch):
        """Must fail if the "worker_app" key were dropped from the payload
        (KeyError) AND must fail if it silently aliased another field's value:
        VCAP_APPLICATION's application_name ("jira-worker") is asserted
        directly against a DIFFERENT shape than instance_id ("jira-worker_0"),
        so a body that only ever set instance_id could not satisfy this."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w", version="1", endpoint="http://x"))
        body = client.payloads[0]
        assert body["worker_app"] == "jira-worker"
        assert body["worker_app"] != body["instance_id"]

    @pytest.mark.asyncio
    async def test_payload_off_cf_sends_blank_worker_app(self, monkeypatch):
        """§ the executor's fallback path: blank here (not missing) is what
        tells the executor to derive from the endpoint host instead."""
        _clear_cf(monkeypatch)
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w", version="1", endpoint="http://x"))
        body = client.payloads[0]
        assert body["worker_app"] == ""

    @pytest.mark.asyncio
    async def test_entity_worker_app_wins_over_the_process(self, monkeypatch):
        """Same precedence rule as instance_id/host/pid/started_at: an embedder
        or test that pins WorkerRegistration.worker_app must not be overridden
        by whatever VCAP_APPLICATION happens to say."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(
            worker_type="w", version="1", endpoint="http://x", worker_app="pinned-app",
        ))
        body = client.payloads[0]
        assert body["worker_app"] == "pinned-app"

    @pytest.mark.asyncio
    async def test_22_jira_worker_registrations_share_one_worker_app(self, monkeypatch):
        """Mirrors TestJiraWorker22Registrations below: one process, 22 node
        types, ALL registrations must report the SAME worker_app — the whole
        point of not keying it on CF_INSTANCE_INDEX."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        r, client = _registry_with_capture()
        for i in range(22):
            await r.register(WorkerRegistration(worker_type=f"jira_op_{i}", version="1", endpoint="http://x"))
        assert {b["worker_app"] for b in client.payloads} == {"jira-worker"}

    @pytest.mark.asyncio
    async def test_started_at_is_frozen_across_registrations(self, monkeypatch):
        """The frozen-constant contract: a process doing N registrations (e.g.
        jira-worker's 22 node types) must report ONE start time, not one per call.
        Proves the code reads instance_identity.STARTED_AT rather than
        recomputing a fresh timestamp per register()."""
        _clear_cf(monkeypatch)
        r, client = _registry_with_capture()
        await r.register(WorkerRegistration(worker_type="w1", version="1", endpoint="http://x"))
        await r.register(WorkerRegistration(worker_type="w2", version="1", endpoint="http://x"))
        first_started_at = client.payloads[0]["started_at"]
        second_started_at = client.payloads[1]["started_at"]
        assert first_started_at == second_started_at
        assert first_started_at == mod.instance_identity.STARTED_AT


class TestJiraWorker22Registrations:
    """Keystone §3.1 reproduced from the SDK side. jira-worker really does
    register 22 node types from ONE process (app/node_types.py:490, 22 _node()
    entries). process -> registration is 1:N, so instance_id CANNOT be the key —
    it is one half of the pair (instance_id, worker_type)."""

    @pytest.mark.asyncio
    async def test_22_registrations_share_one_identity_and_stay_distinct(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        r, client = _registry_with_capture()

        worker_types = [f"jira_op_{i}" for i in range(22)]
        for wt in worker_types:
            await r.register(WorkerRegistration(worker_type=wt, version="1", endpoint="http://x"))

        assert len(client.payloads) == 22

        # §9 checkbox 4 — ONE process: every registration carries the same id...
        assert {b["instance_id"] for b in client.payloads} == {"jira-worker_0"}
        # ...but the 22 registrations stay distinguishable by their other half.
        assert [b["worker_type"] for b in client.payloads] == worker_types
        assert len({b["worker_type"] for b in client.payloads}) == 22
        # The executor derives id = instance_id:worker_type -> 22 distinct rows.
        derived = {f'{b["instance_id"]}:{b["worker_type"]}' for b in client.payloads}
        assert len(derived) == 22
        assert "jira-worker_0:jira_op_0" in derived

    @pytest.mark.asyncio
    async def test_started_at_identical_across_all_22(self, monkeypatch):
        """§9 checkbox 5. A per-call timestamp would make one process look like
        22 processes booted at 22 different moments."""
        _clear_cf(monkeypatch)
        r, client = _registry_with_capture()
        for i in range(22):
            await r.register(WorkerRegistration(worker_type=f"jira_op_{i}", version="1", endpoint="http://x"))
        assert len({b["started_at"] for b in client.payloads}) == 1
        assert len({b["pid"] for b in client.payloads}) == 1

    @pytest.mark.asyncio
    async def test_a_restart_at_the_same_index_reuses_the_identity(self, monkeypatch):
        """Keystone §5's prize row. We cannot restart a process in a unit test, so
        we prove the SDK half: the id is a pure function of the CF env, carrying
        no process-lifetime state (no uuid, no pid) — so a restarted replica at
        the same INDEX re-derives the SAME id and overwrites its own rows.
        Zero orphans. That is what turns R4 from load-bearing into housekeeping.

        The executor-side half (same id -> same row -> unchanged row count) is
        SA-2049 (R2)'s to prove; it cannot be tested from the SDK suite."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")

        r1, c1 = _registry_with_capture()
        await r1.register(WorkerRegistration(worker_type="jira_op_0", version="1", endpoint="http://x"))
        # "restart": a brand-new registry object + a brand-new entity, same env.
        r2, c2 = _registry_with_capture()
        await r2.register(WorkerRegistration(worker_type="jira_op_0", version="1", endpoint="http://x"))

        assert c1.payloads[0]["instance_id"] == c2.payloads[0]["instance_id"] == "jira-worker_0"
