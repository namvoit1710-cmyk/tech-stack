"""Gap-fill edge/boundary/invalid cases for the layer1 value objects + the
request builder + the upstream client's body-decode paths.

Pure domain logic (no network, no mocks) except upstream_http, which is mocked
at the httpx transport seam.
"""
from __future__ import annotations

import httpx
import pytest

from app.layer1_domain.value_objects.app_function_request import (
    InvokeAppFunctionParams,
    build_upstream_request,
)
from app.layer1_domain.value_objects.gateway_request import (
    CallExternalParams,
    CallInternalParams,
    InvalidParams,
)
from app.layer1_domain.value_objects.external_url_policy import (
    ExternalUrlPolicy,
    HostNotAllowed,
    _host_matches,
)
from app.layer4_frameworks.registry_client import ResolvedAppFunction
from app.layer4_frameworks.upstream_http import UpstreamHttpClient


# ── InvokeAppFunctionParams validation (invalid input) ──────────────────

@pytest.mark.unit
def test_invoke_params_rejects_missing_function_id():
    with pytest.raises(InvalidParams, match="function_id"):
        InvokeAppFunctionParams.from_dict({"args": {}})


@pytest.mark.unit
def test_invoke_params_rejects_non_dict_args():
    with pytest.raises(InvalidParams, match="args"):
        InvokeAppFunctionParams.from_dict({"function_id": "f", "args": [1, 2]})


@pytest.mark.unit
def test_invoke_params_rejects_non_dict_headers():
    with pytest.raises(InvalidParams, match="headers"):
        InvokeAppFunctionParams.from_dict({"function_id": "f", "headers": "nope"})


@pytest.mark.unit
def test_invoke_params_rejects_non_positive_timeout():
    with pytest.raises(InvalidParams, match="timeout_ms"):
        InvokeAppFunctionParams.from_dict({"function_id": "f", "timeout_ms": 0})


@pytest.mark.unit
def test_invoke_params_defaults_empty_args_and_headers():
    # boundary: nothing but the id → args/headers default to empty dicts.
    p = InvokeAppFunctionParams.from_dict({"function_id": "  f  "})
    assert p.function_id == "f"  # trimmed
    assert p.args == {} and p.headers == {}
    assert p.timeout_ms is None


# ── build_upstream_request: header-located + multi-body + unfilled path ──

def _resolved(**over):
    base = dict(
        kind="internal", base_url="https://svc.internal", allowed_hosts=(),
        method="POST", path_template="/x", params=(), timeout_ms=0,
        auth_header_name="", auth_header_value="",
    )
    base.update(over)
    return ResolvedAppFunction(**base)


@pytest.mark.unit
def test_build_request_places_header_located_arg():
    r = _resolved(params=({"key": "X-Region", "location": "header", "required": True},))
    b = build_upstream_request(
        r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {"X-Region": "eu"}}),
    )
    assert b.headers["X-Region"] == "eu"


@pytest.mark.unit
def test_build_request_multiple_body_params_collected_into_dict():
    r = _resolved(params=(
        {"key": "a", "location": "body", "required": False},
        {"key": "b", "location": "body", "required": False},
    ))
    b = build_upstream_request(
        r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {"a": 1, "b": 2}}),
    )
    assert b.body == {"a": 1, "b": 2}


@pytest.mark.unit
def test_build_request_multiple_body_params_none_when_no_args_present():
    # boundary: multi-body params but no matching args → collected is empty → None.
    r = _resolved(params=(
        {"key": "a", "location": "body", "required": False},
        {"key": "b", "location": "body", "required": False},
    ))
    b = build_upstream_request(
        r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {}}),
    )
    assert b.body is None


@pytest.mark.unit
def test_build_request_missing_required_header_param_raises():
    r = _resolved(params=({"key": "X-Key", "location": "header", "required": True},))
    with pytest.raises(InvalidParams, match="header"):
        build_upstream_request(
            r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {}}),
        )


@pytest.mark.unit
def test_build_request_unfilled_path_placeholder_raises():
    # An optional path param left unfilled leaves a {placeholder} → rejected.
    r = _resolved(path_template="/orders/{id}",
                  params=({"key": "id", "location": "path", "required": False},))
    with pytest.raises(InvalidParams, match="unfilled path placeholders"):
        build_upstream_request(
            r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {}}),
        )


@pytest.mark.unit
def test_build_request_strips_caller_reserved_headers():
    r = _resolved(auth_header_name="Authorization", auth_header_value="Bearer real")
    b = build_upstream_request(
        r,
        InvokeAppFunctionParams.from_dict({
            "function_id": "f",
            "headers": {"Authorization": "Bearer attacker", "X-Ok": "1"},
        }),
    )
    # caller Authorization stripped; injected auth wins; benign header kept.
    assert b.headers["Authorization"] == "Bearer real"
    assert b.headers["X-Ok"] == "1"


# ── gateway_request extra validation branches ───────────────────────────

@pytest.mark.unit
def test_call_internal_rejects_non_dict_headers():
    with pytest.raises(InvalidParams, match="headers"):
        CallInternalParams.from_dict({"service": "x", "endpoint": "/y", "headers": "nope"})


@pytest.mark.unit
def test_call_internal_accepts_float_timeout_and_coerces_to_int():
    # boundary: float timeout is accepted (positive) and coerced to int.
    p = CallInternalParams.from_dict({"service": "x", "endpoint": "/y", "timeout_ms": 1500.9})
    assert p.timeout_ms == 1500 and isinstance(p.timeout_ms, int)


@pytest.mark.unit
def test_call_external_rejects_non_dict_headers():
    with pytest.raises(InvalidParams, match="headers"):
        CallExternalParams.from_dict({"url": "https://x.com", "headers": 5})


@pytest.mark.unit
def test_call_external_rejects_unknown_method():
    with pytest.raises(InvalidParams, match="method"):
        CallExternalParams.from_dict({"url": "https://x.com", "method": "TRACE"})


@pytest.mark.unit
def test_call_internal_rejects_non_string_service():
    # invalid input: service present but not a string.
    with pytest.raises(InvalidParams, match="service"):
        CallInternalParams.from_dict({"service": 123, "endpoint": "/y"})


@pytest.mark.unit
def test_call_internal_rejects_missing_endpoint():
    # invalid input: service ok but endpoint absent.
    with pytest.raises(InvalidParams, match="endpoint"):
        CallInternalParams.from_dict({"service": "x"})


@pytest.mark.unit
def test_call_external_rejects_non_positive_timeout():
    with pytest.raises(InvalidParams, match="timeout_ms"):
        CallExternalParams.from_dict({"url": "https://x.com", "timeout_ms": -5})


# ── external_url_policy internals ───────────────────────────────────────

@pytest.mark.unit
def test_host_matches_empty_pattern_is_false():
    assert _host_matches("api.partner.com", "") is False


@pytest.mark.unit
def test_check_blocks_when_host_resolves_to_non_ip_string():
    # A resolver returning a non-parsable "IP" → _is_private_ip returns True
    # (fail-closed) → PrivateIpBlocked. Asserts the fail-closed guard.
    from app.layer1_domain.value_objects.external_url_policy import PrivateIpBlocked
    p = ExternalUrlPolicy(
        allowed_hosts=("api.partner.com",), allowed_schemes=("https",),
        resolver=lambda h: "not-an-ip",
    )
    with pytest.raises(PrivateIpBlocked):
        p.check("https://api.partner.com/x")


@pytest.mark.unit
def test_check_empty_allowlist_rejects_everything():
    p = ExternalUrlPolicy(allowed_hosts=(), allowed_schemes=("https",),
                          resolver=lambda h: "8.8.8.8")
    with pytest.raises(HostNotAllowed):
        p.check("https://anything.com/x")


# ── upstream_http body-decode + GET-body-skip ───────────────────────────

class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request):
        self.last_request = request
        return self._handler(request)


def _factory(transport):
    def _f(timeout_s):
        return httpx.AsyncClient(transport=transport, timeout=timeout_s)
    return _f


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_get_with_body_does_not_send_json_body():
    transport = _MockTransport(lambda r: httpx.Response(200, content=b"ok"))
    client = UpstreamHttpClient(
        default_timeout_s=10.0, max_response_bytes=1024,
        http_client_factory=_factory(transport),
    )
    await client.send("GET", "https://x", {}, {"should": "be dropped"}, None)
    # GET carries no request body even when a body arg is supplied.
    assert transport.last_request.content in (b"", None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_json_content_type_but_invalid_json_falls_back_to_text():
    # content-type says JSON but body isn't valid JSON → decode falls back to text.
    def handler(req):
        return httpx.Response(
            200, content=b"{bad json", headers={"content-type": "application/json"},
        )
    client = UpstreamHttpClient(
        default_timeout_s=10.0, max_response_bytes=1024,
        http_client_factory=_factory(_MockTransport(handler)),
    )
    resp = await client.send("GET", "https://x", {}, None, None)
    assert resp.body == "{bad json"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_at_exact_cap_is_allowed():
    # boundary: body exactly == cap is NOT too large (strict > check).
    def handler(req):
        return httpx.Response(200, content=b"a" * 1024)
    client = UpstreamHttpClient(
        default_timeout_s=10.0, max_response_bytes=1024,
        http_client_factory=_factory(_MockTransport(handler)),
    )
    resp = await client.send("GET", "https://x", {}, None, None)
    assert resp.status_code == 200
    assert resp.body == "a" * 1024
