"""NodeSpec save-time validation over HTTP (integration matrix Module 1).

End-to-end contract of ``NODE_CONFIG_VALIDATION`` through the real
controllers: POST /api/v1/workflows, PUT/PATCH /api/v1/workflows/{id}.
Enforce-mode failures must surface ``NODE_CONFIG_INVALID`` with the
per-field ``violations[]`` payload the FE renders (design §8); warn-only
violations must never block; worker-registry problems must never block.
"""

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_error, assert_ok

MODE_ENV = "NODE_CONFIG_VALIDATION"


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _trigger(node_id="t1", **params):
    return {
        "id": node_id, "name": node_id, "node_type": "TRIGGER",
        "parameters": {"trigger_type": "manual", **params},
    }


def _post_workflow(client, nodes, name="wf-v"):
    return client.post("/api/v1/workflows", json={"name": name, "nodes": nodes})


def _violations(response):
    err = assert_error(response, code="NODE_CONFIG_INVALID")
    assert response.status_code == 400
    violations = err["details"]["violations"]
    assert violations, "violations[] must be non-empty"
    for v in violations:
        assert {"node_id", "key", "code", "message", "severity"} <= set(v)
    return violations


# ── V1: invalid enum → 400 with full violations payload, multi-node ──


def test_enforce_invalid_enum_collects_all_nodes(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "wait_some"}},
        {"id": "a1", "name": "A", "node_type": "APPROVAL_FLOW",
         "parameters": {}},  # change_request_id missing (required, no default)
    ])
    violations = _violations(r)
    by_node = {v["node_id"]: v for v in violations}
    assert by_node["m1"]["key"] == "merge_strategy"
    assert by_node["m1"]["code"] == "invalid_enum"
    assert "all|any|n_of_m" in by_node["m1"]["message"]
    assert by_node["a1"]["key"] == "change_request_id"
    assert by_node["a1"]["code"] == "missing_required"


# ── V2: conditionally-required (visible_when) over HTTP ──


def test_enforce_trigger_conditional_required(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [{
        "id": "t1", "name": "T", "node_type": "TRIGGER",
        "parameters": {"trigger_type": "schedule"},  # no schedule_cron
    }])
    violations = _violations(r)
    assert violations[0]["key"] == "schedule_cron"
    assert violations[0]["code"] == "missing_required"

    r = _post_workflow(client, [_trigger()], name="wf-manual")
    body = assert_ok(r)
    assert body["id"]


# ── V3: warn-only violations never block (bare CONDITION draft) ──


def test_enforce_saves_bare_condition_draft(client, container, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [
        _trigger(),
        {"id": "c1", "name": "C", "node_type": "CONDITION", "parameters": {}},
    ], name="wf-draft")
    body = assert_ok(r)
    stored = container["_infra"].repos.workflow.get_by_id(body["id"])
    assert stored is not None and stored.get_node("c1") is not None


# ── V4: exclusive group both-set → hard error ──


def test_enforce_condition_both_data_and_expression(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [{
        "id": "c1", "name": "C", "node_type": "CONDITION",
        "parameters": {
            "data": [{"key": "{{$input.n}}", "operations": ">", "value": 1}],
            "expression": "1 == 1",
        },
    }])
    violations = _violations(r)
    assert violations[0]["code"] == "exclusive_group"
    assert violations[0]["severity"] == "error"


# ── V5: malformed template expression ──


def test_enforce_invalid_expression_syntax(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [{
        "id": "x1", "name": "X", "node_type": "COMPUTE",
        "parameters": {"expression": "{{ $a.b }}"},  # spaces inside braces
    }])
    violations = _violations(r)
    assert violations[0]["key"] == "expression"
    assert violations[0]["code"] == "invalid_expression"


# ── V6: unknown config key via explicit (API-authored) config ──


def test_enforce_unknown_explicit_config_key(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    r = _post_workflow(client, [{
        "id": "m1", "name": "M", "node_type": "MERGE",
        "config": {"merge_strategy": "all", "typo_key": 1},
    }])
    violations = _violations(r)
    assert violations[0]["key"] == "typo_key"
    assert violations[0]["code"] == "unknown_key"


# ── V7: enforce on update — PUT replace and PATCH nodes ──


def _create_valid(client, name="wf-upd"):
    body = assert_ok(_post_workflow(client, [_trigger()], name=name))
    return body["id"], body.get("version", "")


def test_enforce_put_replace_with_invalid_node(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    wf_id, version = _create_valid(client)
    r = client.put(f"/api/v1/workflows/{wf_id}", json={
        "name": "wf-upd", "version": version,
        "nodes": [{"id": "m1", "name": "M", "node_type": "MERGE",
                   "parameters": {"merge_strategy": "wait_some"}}],
        "edges": [],
    })
    violations = _violations(r)
    assert violations[0]["code"] == "invalid_enum"


def test_enforce_patch_with_invalid_node(client, monkeypatch):
    monkeypatch.setenv(MODE_ENV, "enforce")
    wf_id, _ = _create_valid(client, name="wf-patch")
    r = client.patch(f"/api/v1/workflows/{wf_id}", json={
        "nodes": [{"id": "m1", "name": "M", "node_type": "MERGE",
                   "parameters": {"merge_strategy": "wait_some"}}],
    })
    violations = _violations(r)
    assert violations[0]["code"] == "invalid_enum"


# ── V8: metadata-only PATCH re-validates stored nodes (pinned) ──


def test_enforce_metadata_patch_revalidates_stored_invalid_nodes(
    client, monkeypatch,
):
    """Pinned clean-break semantics: validation runs on the WHOLE
    workflow on every save path, so a workflow that slipped in during
    the warn window 400s even on an unrelated rename. Residual-invalid
    workflows are surfaced by the migration report and must be fixed
    (db/migrations/042_normalize_node_config.md step 2)."""
    monkeypatch.setenv(MODE_ENV, "warn")
    body = assert_ok(_post_workflow(client, [
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "wait_some"}},
    ], name="wf-residual"))
    wf_id = body["id"]

    monkeypatch.setenv(MODE_ENV, "enforce")
    r = client.patch(f"/api/v1/workflows/{wf_id}", json={"name": "renamed"})
    violations = _violations(r)
    assert violations[0]["node_id"] == "m1"


# ── V9: warn default saves invalid config ──


def test_warn_default_saves_invalid_enum(client, container, monkeypatch):
    monkeypatch.delenv(MODE_ENV, raising=False)  # default = warn
    body = assert_ok(_post_workflow(client, [
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "wait_some"}},
    ], name="wf-warn"))
    stored = container["_infra"].repos.workflow.get_by_id(body["id"])
    assert stored.get_node("m1").config["merge_strategy"] == "wait_some"


# ── V10/V11: worker-registry interplay ──


class _RegistryStub:
    """Worker registry returning a declared input schema; counts lookups."""

    def __init__(self, raise_on_lookup=False):
        self.lookups = 0
        self._raise = raise_on_lookup

    def get_worker_by_type(self, worker_type):
        self.lookups += 1
        if self._raise:
            raise RuntimeError("registry down")
        return {
            "worker_type": worker_type,
            "name": worker_type,
            "input_schema": [
                {"key": "url", "outputType": "string",
                 "fieldConfig": {"wrapperProps": {"label": "URL"}}},
            ],
        }

    def list_workers(self):
        return []


def test_enforce_worker_unknown_binding_is_warn_only(
    client, container, monkeypatch,
):
    monkeypatch.setenv(MODE_ENV, "enforce")
    stub = _RegistryStub()
    container["create_workflow"].worker_registry_client = stub
    r = _post_workflow(client, [{
        "id": "w1", "name": "W", "node_type": "TASK", "worker_type": "http",
        "parameters": {"url": "https://x", "typo_binding": 1},
    }], name="wf-worker")
    assert_ok(r)
    # Positive twin: the resolver actually consulted the registry —
    # without this the warn-only claim would pass vacuously.
    assert stub.lookups >= 1


def test_enforce_registry_outage_never_blocks_save(
    client, container, monkeypatch,
):
    monkeypatch.setenv(MODE_ENV, "enforce")
    stub = _RegistryStub(raise_on_lookup=True)
    container["create_workflow"].worker_registry_client = stub
    r = _post_workflow(client, [{
        "id": "w1", "name": "W", "node_type": "TASK", "worker_type": "http",
        "parameters": {"url": "https://x"},
    }], name="wf-outage")
    assert_ok(r)
    assert stub.lookups >= 1  # it tried, failed, degraded — no 400/500
