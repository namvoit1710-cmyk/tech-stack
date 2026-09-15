"""REST tests for the name-addressed worker resolve route.

    POST /api/v1/gateway/functions/_resolve                {"app": …, "function": …}
    POST /api/v1/gateway/functions/{function_id}/_resolve   (pre-existing)

Both are declared on the same router. `/_resolve` is one path segment and
`/{function_id}/_resolve` is two, so they cannot shadow each other — but that is
exactly the kind of thing that breaks silently on a refactor, so it is pinned here.
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


@pytest.fixture
def seeded(client):
    """A gateway app 'gov' with one function 'get_order'."""
    assert_ok(client.post("/api/v1/gateway/apps", json={
        "name": "gov",
        "kind": "internal",
        "base_url": "https://gov.internal",
        "auth_type": "bearer",
        "auth_config": {"token": "s3cret"},
    }))
    created = assert_ok(client.post("/api/v1/gateway/apps/gov/functions", json={
        "name": "get_order",
        "method": "GET",
        "path_template": "/orders/{order_id}",
        "input_params": [{"key": "order_id", "location": "path", "required": True}],
    }))
    return created["id"]


def _by_name(client, app, function):
    return client.post(
        "/api/v1/gateway/functions/_resolve", json={"app": app, "function": function}
    )


def test_resolve_by_name_returns_the_call_spec(client, seeded):
    data = assert_ok(_by_name(client, "gov", "get_order"))
    assert data["kind"] == "internal"
    assert data["base_url"] == "https://gov.internal"
    assert data["method"] == "GET"
    assert data["path_template"] == "/orders/{order_id}"
    assert data["params"][0]["key"] == "order_id"


def test_resolve_by_name_matches_resolve_by_id_byte_for_byte(client, seeded):
    """The two routes are two addresses for one thing — they must not drift."""
    by_name = assert_ok(_by_name(client, "gov", "get_order"))
    by_id = assert_ok(client.post(f"/api/v1/gateway/functions/{seeded}/_resolve"))
    assert by_name == by_id


def test_the_literal_route_does_not_shadow_the_id_route(client, seeded):
    """`/_resolve` must not swallow `/{function_id}/_resolve`, and vice-versa."""
    assert client.post(f"/api/v1/gateway/functions/{seeded}/_resolve").status_code == 200
    assert _by_name(client, "gov", "get_order").status_code == 200


def test_unknown_app_is_404(client, seeded):
    assert_error(_by_name(client, "nope", "get_order"), code="GATEWAY_APP_NOT_FOUND")


def test_unknown_function_is_404(client, seeded):
    assert_error(_by_name(client, "gov", "nope"), code="GATEWAY_FUNCTION_NOT_FOUND")


def test_missing_function_key_is_422_from_the_dto(client, seeded):
    """`app`/`function` are required by the request model — FastAPI rejects before the use case."""
    assert client.post("/api/v1/gateway/functions/_resolve", json={"app": "gov"}).status_code == 422


def test_deleted_function_is_no_longer_resolvable_by_name(client, seeded):
    assert_ok(_by_name(client, "gov", "get_order"))
    client.delete("/api/v1/gateway/apps/gov/functions/get_order")
    assert_error(_by_name(client, "gov", "get_order"), code="GATEWAY_FUNCTION_NOT_FOUND")
