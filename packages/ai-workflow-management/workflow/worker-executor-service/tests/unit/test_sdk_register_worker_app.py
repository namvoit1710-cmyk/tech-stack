"""SA-2047 R14 (Task 11) -- `worker_app` must survive the SDK register HTTP door.

WorkerRegistration.__post_init__ (Task 2) already does the right thing on the
entity: a supplied `worker_app` wins, a blank one is derived from
`endpoint_url` via `worker_app_from_endpoint`. But `SdkRegisterWorkerInputDto`
never declared the field -- and Pydantic silently drops whatever a model does
not declare. That is the EXACT defect the `kind` comment in
worker_controller.py records (SA-1735: every connector registered as the
default "action" because Pydantic dropped `kind` before it reached the use
case). `worker_app` is the same class of bug: an SDK sending it today has it
thrown away before the entity ever sees it, and the caller silently gets the
derived-from-host value instead of the one it asked for.

Mirrors test_replicas_routes.py / test_worker_apps_routes.py's `_client()` /
`_unwrap()` fixture shape deliberately: a TestClient wired to the SAME
`_registry_store` the route reads from (not a fresh one), and the STORED
ENTITY is asserted on directly rather than the response body, so a bug that
only breaks persistence (and not the echo) cannot hide behind a passing
assertion.

THE DISCRIMINATION TRAP: __post_init__'s fallback means a registration ends
up with a non-empty worker_app whether or not the DTO carries the field --
either the caller's value survives, or derivation fills the gap. A test that
only asserts "worker_app is truthy" passes either way and proves nothing. Both
tests below register through the SAME endpoint host
(smdg-ai-tenant-1-worker-jira-worker.cfapps...), which derives to
"smdg-ai-tenant-1-worker-jira-worker" -- deliberately DIFFERENT from the
supplied "jira-worker" the first test sends. If the DTO still dropped
worker_app, the first test would see the DERIVED string where it expects the
SUPPLIED one, and fail loudly instead of passing on a technicality.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient

from main import create_app
from bootstrap import build_app_container

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
DERIVED_APP = "smdg-ai-tenant-1-worker-jira-worker"  # first DNS label of JIRA


def _client():
    container = build_app_container()
    app = create_app()
    app.state.container = container
    return TestClient(app), container["_registry_store"]


def _unwrap(resp):
    body = resp.json()
    assert body["status"] == "ok", body
    return body["data"]


def test_sdk_supplied_worker_app_is_stored():
    """The regression: posting worker_app="jira-worker" MUST NOT come back as
    the derived "smdg-ai-tenant-1-worker-jira-worker" -- that would mean
    Pydantic dropped the field exactly as it once dropped `kind`."""
    client, store = _client()
    resp = client.post("/api/v1/workers/register", json={
        "worker_type": "jira_create_issue",
        "worker_app": "jira-worker",
        "instance_id": "jira-worker_0",
        "endpoint": JIRA,
    })
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    _unwrap(resp)  # confirms the envelope before trusting the store read below

    stored = store.get("jira-worker_0:jira_create_issue")
    assert stored is not None, "registration did not land under the expected id"
    assert stored.worker_app == "jira-worker"


def test_absent_worker_app_falls_back_to_derivation():
    """proto3-additive companion: an SDK that sends nothing still registers,
    and the executor derives the app from the endpoint host -- unchanged
    behavior from before this task, pinned here so a future edit to the
    mapping cannot silently break the fallback while chasing the supplied
    case above."""
    client, store = _client()
    resp = client.post("/api/v1/workers/register", json={
        "worker_type": "jira_get_issue",
        "instance_id": "jira-worker_0",
        "endpoint": JIRA,
    })
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    _unwrap(resp)

    stored = store.get("jira-worker_0:jira_get_issue")
    assert stored is not None, "registration did not land under the expected id"
    assert stored.worker_app == DERIVED_APP
