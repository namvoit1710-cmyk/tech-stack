"""SA-1735 gap: `kind` must survive the HTTP registration door.

Why this file exists — the bug it was written to catch:

`test_sdk_register_worker_kind.py` builds a ``SdkRegisterWorkerInput`` **directly** and
proves the use case, the entity, the store and the gRPC servicer all carry ``kind``. Every
one of those tests passed. But the worker-sdk does not register over gRPC — ``bootstrap.py``
wires ``HttpWorkerRegistry``, which POSTs to ``/api/v1/workers/register``. That request is
parsed by ``SdkRegisterWorkerInputDto``, a Pydantic model that did **not declare `kind`** —
so Pydantic silently dropped it and every connector node registered as the default
``"action"``. The one path actually used in production was the one path with no test.

These tests go through the real HTTP door, exactly as the SDK does.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _payload(worker_type: str, **extra):
    """The body HttpWorkerRegistry.register() actually posts (see worker-sdk)."""
    body = {
        "worker_type": worker_type,
        "version": "1.0.0",
        "spec_version": "1.0.0",
        "endpoint": "http://localhost:38002",
        "sdk_version": "1.0.0",
        "input_schema": [],
        "output_schema": [],
        "name": worker_type,
        "description": "",
        "node_class": "BUSINESS",
        "icon": "Cog",
        "color": "#0052CC",
        "tags": [],
        "capabilities": [],
        "ports": {"in": [{"id": "in", "label": "In"}], "out": [{"id": "out", "label": "Out"}]},
        "functions": [],
    }
    body.update(extra)
    return body


def _kind_of(client, worker_type: str):
    r = client.get("/api/v1/workers")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("data", body)
    if isinstance(items, dict):
        items = items.get("workers") or []
    for w in items:
        if w.get("worker_type") == worker_type:
            return w.get("kind")
    raise AssertionError(f"{worker_type} not registered: {body}")


class TestKindSurvivesHttpRegistration:
    def test_read_kind_survives_the_dto(self, client):
        """The regression. Posting kind="read" MUST NOT come back as "action"."""
        r = client.post("/api/v1/workers/register", json=_payload("jira_get_issue", kind="read"))
        assert r.status_code in (200, 201), r.text
        assert _kind_of(client, "jira_get_issue") == "read"

    def test_util_kind_survives_the_dto(self, client):
        r = client.post("/api/v1/workers/register", json=_payload("jira_myself", kind="util"))
        assert r.status_code in (200, 201), r.text
        assert _kind_of(client, "jira_myself") == "util"

    def test_kind_defaults_to_action_when_the_worker_sends_none(self, client):
        """An older SDK that does not send `kind` must still register (proto3-additive)."""
        r = client.post("/api/v1/workers/register", json=_payload("legacy_worker"))
        assert r.status_code in (200, 201), r.text
        assert _kind_of(client, "legacy_worker") == "action"

    def test_invalid_kind_rejected_with_422(self, client):
        """SA-1734 blocker fix: the executor is the network boundary — it receives
        registrations off the wire before the control plane or the frontend ever
        see them, and it had no vocabulary at all. An invented kind (e.g. "write",
        which shipped inside this same PR — see worker_registration.py) must be
        rejected here, not silently coerced or passed through to
        worker_node_provider as a bogus "Jira.banana" category."""
        r = client.post("/api/v1/workers/register", json=_payload("jira_banana_worker", kind="banana"))
        assert r.status_code == 422, r.text

        r2 = client.get("/api/v1/workers")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        items = body.get("data", body)
        if isinstance(items, dict):
            items = items.get("workers") or []
        assert not any(w.get("worker_type") == "jira_banana_worker" for w in items), (
            "a worker registering with an unknown kind must NOT be registered"
        )

    def test_the_dto_actually_declares_kind(self):
        """Guards the exact failure: a Pydantic model silently drops undeclared fields, so a
        missing `kind` field on the DTO is invisible at every other layer."""
        from app.layer3_adapters.controllers.restful.v1.worker_controller import (
            SdkRegisterWorkerInputDto,
        )

        assert "kind" in SdkRegisterWorkerInputDto.model_fields, (
            "SdkRegisterWorkerInputDto must declare `kind` — Pydantic drops what it does not "
            "declare, and HttpWorkerRegistry is the path the SDK actually uses."
        )
