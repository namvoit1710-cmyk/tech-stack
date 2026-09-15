"""End-to-end REST tests for the gateway app + function registry (P1).

Uses build_app_container() (mock infra → credential cipher dev-fallback so the
gateway features load) + TestClient. Covers create/get/list/update(CAS)/delete
for both apps and functions, credential redaction, and the key error paths.
"""
import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok, assert_error


@pytest.fixture
def client():
    container = build_app_container()
    return TestClient(create_fastapi_app(container))


def _create_app(client, name="gov", **over):
    body = {
        "name": name,
        "kind": "internal",
        "base_url": "https://gov.internal",
        "auth_type": "bearer",
        "auth_config": {"token": "s3cret"},
        "description": "core gov",
    }
    body.update(over)
    return client.post("/api/v1/gateway/apps", json=body)


# ── Apps ──────────────────────────────────────────────────────────────

def test_create_get_app_redacts_credential(client):
    created = assert_ok(_create_app(client))
    assert created["id"] and created["version"] == 1

    got = assert_ok(client.get("/api/v1/gateway/apps/gov"))["app"]
    assert got["name"] == "gov" and got["kind"] == "internal"
    assert got["auth_type"] == "bearer"
    assert got["auth_summary"]["credential_present"] is True
    # The secret must never be exposed.
    assert "s3cret" not in str(got)
    assert "auth_config" not in got


def test_create_app_duplicate_name_conflicts(client):
    assert_ok(_create_app(client))
    assert_error(client.post("/api/v1/gateway/apps", json={
        "name": "gov", "kind": "internal", "base_url": "https://x",
    }), code="GATEWAY_APP_NAME_TAKEN")


def test_create_app_invalid_auth_config_rejected(client):
    assert_error(client.post("/api/v1/gateway/apps", json={
        "name": "bad", "kind": "internal", "base_url": "https://x",
        "auth_type": "bearer", "auth_config": {},
    }), code="VALIDATION_ERROR")


def test_create_external_app_requires_allowed_hosts(client):
    assert_error(client.post("/api/v1/gateway/apps", json={
        "name": "ext", "kind": "external",
    }), code="VALIDATION_ERROR")
    ok = assert_ok(client.post("/api/v1/gateway/apps", json={
        "name": "ext", "kind": "external", "allowed_hosts": ["api.partner.com"],
    }))
    assert ok["version"] == 1


def test_update_app_cas(client):
    created = assert_ok(_create_app(client))
    v = created["version"]
    # Missing If-Match → 409 PRECONDITION_REQUIRED
    assert_error(
        client.patch("/api/v1/gateway/apps/gov", json={"base_url": "https://gov2"}),
    )
    # Correct version → updates, bumps version
    updated = assert_ok(client.patch(
        "/api/v1/gateway/apps/gov",
        json={"base_url": "https://gov2"},
        headers={"If-Match": str(v)},
    ))
    assert updated["version"] == v + 1
    # Stale version → 409
    assert_error(client.patch(
        "/api/v1/gateway/apps/gov",
        json={"base_url": "https://gov3"},
        headers={"If-Match": str(v)},
    ), code="OPTIMISTIC_CONCURRENCY_CONFLICT")


def test_delete_app_soft(client):
    assert_ok(_create_app(client))
    assert_ok(client.delete("/api/v1/gateway/apps/gov"))
    assert_error(client.get("/api/v1/gateway/apps/gov"), code="GATEWAY_APP_NOT_FOUND")


# ── Functions ───────────────────────────────────────────────────────────

def _create_fn(client, app="gov", name="get_order", **over):
    body = {
        "name": name,
        "method": "GET",
        "path_template": "/orders/{order_id}",
        "input_params": [
            {"key": "order_id", "type": "string", "location": "path", "required": True},
        ],
        "output_fields": [{"key": "status_code", "type": "number"}],
    }
    body.update(over)
    return client.post(f"/api/v1/gateway/apps/{app}/functions", json=body)


def test_function_lifecycle(client):
    assert_ok(_create_app(client))
    created = assert_ok(_create_fn(client))
    assert created["id"] and created["version"] == 1

    got = assert_ok(client.get("/api/v1/gateway/apps/gov/functions/get_order"))["function"]
    assert got["method"] == "GET" and got["path_template"] == "/orders/{order_id}"
    assert got["input_params"][0]["location"] == "path"

    listed = assert_ok(client.get("/api/v1/gateway/apps/gov/functions"))
    assert [f["name"] for f in listed["items"]] == ["get_order"]
    assert listed["total"] == 1

    # Atomic signature change via PATCH (If-Match required).
    updated = assert_ok(client.patch(
        "/api/v1/gateway/apps/gov/functions/get_order",
        json={
            "path_template": "/orders/{oid}",
            "input_params": [{"key": "oid", "location": "path", "required": True}],
        },
        headers={"If-Match": str(got["version"])},
    ))
    assert updated["version"] == got["version"] + 1

    assert_ok(client.delete("/api/v1/gateway/apps/gov/functions/get_order"))
    assert_error(
        client.get("/api/v1/gateway/apps/gov/functions/get_order"),
        code="GATEWAY_FUNCTION_NOT_FOUND",
    )


def test_function_on_missing_app_404(client):
    assert_error(_create_fn(client, app="nope"), code="GATEWAY_APP_NOT_FOUND")


def test_function_bad_signature_rejected(client):
    assert_ok(_create_app(client))
    # path placeholder with no matching path param
    assert_error(client.post("/api/v1/gateway/apps/gov/functions", json={
        "name": "bad", "method": "GET", "path_template": "/x/{y}",
    }), code="VALIDATION_ERROR")


def test_function_duplicate_name_conflicts(client):
    assert_ok(_create_app(client))
    assert_ok(_create_fn(client))
    assert_error(_create_fn(client), code="GATEWAY_FUNCTION_NAME_TAKEN")


# ── P2: worker-facing resolve + operator test ──────────────────────────

def test_resolve_returns_decrypted_callspec(client):
    assert_ok(_create_app(client))                # gov, bearer token "s3cret"
    created = assert_ok(_create_fn(client))       # get_order, GET /orders/{order_id}
    data = assert_ok(client.post(f"/api/v1/gateway/functions/{created['id']}/_resolve"))
    assert data["kind"] == "internal" and data["base_url"] == "https://gov.internal"
    assert data["method"] == "GET" and data["path_template"] == "/orders/{order_id}"
    assert data["params"][0]["location"] == "path"
    # The decrypted credential is exposed ONLY on this worker-facing endpoint.
    assert data["auth_header_name"] == "Authorization"
    assert data["auth_header_value"] == "Bearer s3cret"
    # ...and never on the admin GET.
    got = assert_ok(client.get("/api/v1/gateway/apps/gov"))["app"]
    assert "s3cret" not in str(got)


def test_resolve_unknown_function_404(client):
    assert_error(
        client.post("/api/v1/gateway/functions/does-not-exist/_resolve"),
        code="GATEWAY_FUNCTION_NOT_FOUND",
    )


def test_app_test_endpoint_returns_result(client):
    assert_ok(_create_app(client, name="probe-app", auth_type="none", auth_config={}))
    data = assert_ok(client.post("/api/v1/gateway/apps/probe-app/test", json={"timeout_s": 1.0}))
    assert "reachable" in data  # base_url is unreachable in tests; we assert shape only


# ── App-delete cascade ──────────────────────────────────────────────────

def test_delete_app_cascades_to_functions(client):
    assert_ok(_create_app(client))
    assert_ok(_create_fn(client))
    assert_ok(client.delete("/api/v1/gateway/apps/gov"))
    # The app's function is soft-deleted along with the app.
    assert_error(
        client.get("/api/v1/gateway/apps/gov/functions/get_order"),
        code="GATEWAY_APP_NOT_FOUND",
    )


# ── P4: external functions (per-app allowlist + author-time enforcement) ─

def _create_external_app(client, name="partner", hosts=("api.partner.com",)):
    return client.post("/api/v1/gateway/apps", json={
        "name": name, "kind": "external", "allowed_hosts": list(hosts),
    })


def test_external_function_lifecycle_and_resolve(client):
    assert_ok(_create_external_app(client))
    created = assert_ok(client.post("/api/v1/gateway/apps/partner/functions", json={
        "name": "get_order", "method": "GET",
        "path_template": "https://api.partner.com/v1/orders/{order_id}",
        "input_params": [{"key": "order_id", "location": "path", "required": True}],
        "output_fields": [{"key": "id", "type": "string"}],
    }))
    # Worker resolve returns the external call-spec with the per-app allowlist.
    data = assert_ok(client.post(f"/api/v1/gateway/functions/{created['id']}/_resolve"))
    assert data["kind"] == "external"
    assert data["allowed_hosts"] == ["api.partner.com"]
    assert data["path_template"] == "https://api.partner.com/v1/orders/{order_id}"


def test_external_function_host_not_in_allowlist_rejected_at_create(client):
    assert_ok(_create_external_app(client))
    assert_error(client.post("/api/v1/gateway/apps/partner/functions", json={
        "name": "evil", "method": "GET",
        "path_template": "https://evil.example.com/x",
    }), code="VALIDATION_ERROR")


def test_external_function_relative_path_rejected(client):
    assert_ok(_create_external_app(client))
    assert_error(client.post("/api/v1/gateway/apps/partner/functions", json={
        "name": "rel", "method": "GET", "path_template": "/v1/orders",
    }), code="VALIDATION_ERROR")


def test_internal_function_absolute_url_rejected(client):
    assert_ok(_create_app(client))  # internal "gov"
    assert_error(client.post("/api/v1/gateway/apps/gov/functions", json={
        "name": "abs", "method": "GET", "path_template": "https://api.partner.com/x",
    }), code="VALIDATION_ERROR")
