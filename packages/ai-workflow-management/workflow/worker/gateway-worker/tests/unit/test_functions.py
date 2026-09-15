"""Unit tests for the call_internal and call_external handlers.

The registry client and upstream HTTP layer are replaced with stubs so we can
exercise routing/header-merging/error-mapping logic without real network I/O.
"""
from __future__ import annotations

import pytest

from app.functions import (
    _make_call_external_handler,
    _make_call_internal_handler,
    build_gateway_worker_functions,
)
from app.layer1_domain.value_objects.external_url_policy import (
    ExternalUrlPolicy,
    HostNotAllowed,
    PrivateIpBlocked,
    SchemeNotAllowed,
)
from app.layer4_frameworks.registry_client import (
    RegistryUnavailable,
    ResolvedService,
    ServiceNotFound,
)
from app.layer4_frameworks.upstream_http import (
    ResponseTooLarge,
    UpstreamError,
    UpstreamResponse,
    UpstreamTimeout,
)


class _StubRegistry:
    def __init__(self, resolved=None, error=None):
        self._resolved = resolved
        self._error = error
        self.calls: list[str] = []

    async def resolve(self, name):
        self.calls.append(name)
        if self._error:
            raise self._error
        return self._resolved


class _StubUpstream:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    async def send(self, *, method, url, headers, body, timeout_ms):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout_ms": timeout_ms}
        )
        if self._error:
            raise self._error
        return self._response


def _resolved_bearer():
    return ResolvedService(
        base_url="https://gov.internal",
        auth_header_name="Authorization",
        auth_header_value="Bearer abc.def",
        auth_type="bearer",
    )


def _ok_response(**overrides):
    base = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": {"ok": True},
    }
    base.update(overrides)
    return UpstreamResponse(**base)


# ── call_internal ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_injects_auth_header_and_returns_response():
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(response=_ok_response())
    handler = _make_call_internal_handler(registry, upstream)

    result = await handler({
        "service": "governance",
        "endpoint": "/tasks/123",
        "method": "GET",
    })

    assert result["status_code"] == 200
    assert result["body"] == {"ok": True}
    assert upstream.calls[0]["url"] == "https://gov.internal/tasks/123"
    assert upstream.calls[0]["headers"] == {"Authorization": "Bearer abc.def"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_strips_caller_reserved_headers():
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(response=_ok_response())
    handler = _make_call_internal_handler(registry, upstream)

    await handler({
        "service": "governance",
        "endpoint": "/x",
        "headers": {
            "Authorization": "Bearer attacker",  # should be stripped
            "Cookie": "session=evil",            # should be stripped
            "X-Trace-Id": "abc",                 # should pass through
        },
    })
    headers = upstream.calls[0]["headers"]
    # Registry's Authorization wins
    assert headers["Authorization"] == "Bearer abc.def"
    assert "Cookie" not in headers
    assert headers["X-Trace-Id"] == "abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_passes_endpoint_with_leading_slash_correctly():
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(response=_ok_response())
    handler = _make_call_internal_handler(registry, upstream)

    await handler({"service": "x", "endpoint": "tasks/123"})
    assert upstream.calls[0]["url"] == "https://gov.internal/tasks/123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_bad_params_returns_bad_request():
    handler = _make_call_internal_handler(_StubRegistry(), _StubUpstream())
    result = await handler({"endpoint": "/x"})  # missing service
    assert result == {"error": {"code": "BAD_REQUEST", "message": result["error"]["message"]}}
    assert "service" in result["error"]["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_service_not_found():
    registry = _StubRegistry(error=ServiceNotFound("ghost not found"))
    handler = _make_call_internal_handler(registry, _StubUpstream())
    result = await handler({"service": "ghost", "endpoint": "/x"})
    assert result["error"]["code"] == "SERVICE_NOT_FOUND"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_registry_unavailable():
    registry = _StubRegistry(error=RegistryUnavailable("cp down"))
    handler = _make_call_internal_handler(registry, _StubUpstream())
    result = await handler({"service": "x", "endpoint": "/y"})
    assert result["error"]["code"] == "REGISTRY_UNAVAILABLE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_upstream_timeout():
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(error=UpstreamTimeout("slow"))
    handler = _make_call_internal_handler(registry, upstream)
    result = await handler({"service": "x", "endpoint": "/y"})
    assert result["error"]["code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_response_too_large():
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(error=ResponseTooLarge("big"))
    handler = _make_call_internal_handler(registry, upstream)
    result = await handler({"service": "x", "endpoint": "/y"})
    assert result["error"]["code"] == "RESPONSE_TOO_LARGE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_upstream_500_is_not_a_gateway_error():
    """Upstream non-2xx → status_code passed through, NO `error` field."""
    registry = _StubRegistry(resolved=_resolved_bearer())
    upstream = _StubUpstream(response=_ok_response(status_code=502, body={"detail": "bad gw"}))
    handler = _make_call_internal_handler(registry, upstream)
    result = await handler({"service": "x", "endpoint": "/y"})
    assert "error" not in result
    assert result["status_code"] == 502
    assert result["body"] == {"detail": "bad gw"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_internal_no_auth_when_none_type():
    registry = _StubRegistry(resolved=ResolvedService(
        base_url="https://public.example.com",
        auth_header_name="",
        auth_header_value="",
        auth_type="none",
    ))
    upstream = _StubUpstream(response=_ok_response())
    handler = _make_call_internal_handler(registry, upstream)
    await handler({"service": "public", "endpoint": "/x"})
    assert upstream.calls[0]["headers"] == {}


# ── call_external ──────────────────────────────────────────────────────


def _policy(*hosts, resolver=None):
    return ExternalUrlPolicy(
        allowed_hosts=tuple(hosts),
        allowed_schemes=("https",),
        resolver=resolver or (lambda h: "8.8.8.8"),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_happy_path():
    upstream = _StubUpstream(response=_ok_response())
    handler = _make_call_external_handler(_policy("api.partner.com"), upstream)
    result = await handler({
        "url": "https://api.partner.com/lookup",
        "method": "POST",
        "body": {"q": "x"},
        "headers": {"X-Trace": "abc"},
    })
    assert result["status_code"] == 200
    assert upstream.calls[0]["url"] == "https://api.partner.com/lookup"
    # External path does NOT strip caller headers; passes through as-is.
    assert upstream.calls[0]["headers"] == {"X-Trace": "abc"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_unknown_host_blocked():
    upstream = _StubUpstream()
    handler = _make_call_external_handler(_policy("api.partner.com"), upstream)
    result = await handler({"url": "https://api.intruder.com/x"})
    assert result["error"]["code"] == "HOST_NOT_ALLOWED"
    # No upstream call attempted
    assert upstream.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_non_https_blocked():
    upstream = _StubUpstream()
    handler = _make_call_external_handler(_policy("api.partner.com"), upstream)
    result = await handler({"url": "http://api.partner.com/x"})
    assert result["error"]["code"] == "SCHEME_NOT_ALLOWED"
    assert upstream.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_private_ip_blocked():
    upstream = _StubUpstream()
    handler = _make_call_external_handler(
        _policy("api.partner.com", resolver=lambda h: "10.0.0.1"),
        upstream,
    )
    result = await handler({"url": "https://api.partner.com/x"})
    assert result["error"]["code"] == "PRIVATE_IP_BLOCKED"
    assert upstream.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_upstream_500_passes_through():
    upstream = _StubUpstream(response=_ok_response(status_code=500))
    handler = _make_call_external_handler(_policy("api.partner.com"), upstream)
    result = await handler({"url": "https://api.partner.com/x"})
    assert "error" not in result
    assert result["status_code"] == 500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_external_upstream_network_error():
    upstream = _StubUpstream(error=UpstreamError("dns"))
    handler = _make_call_external_handler(_policy("api.partner.com"), upstream)
    result = await handler({"url": "https://api.partner.com/x"})
    assert result["error"]["code"] == "UPSTREAM_ERROR"


# ── factory / wiring ──────────────────────────────────────────────────


@pytest.mark.unit
def test_build_gateway_worker_functions_returns_both(tmp_path):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowed_hosts: [api.partner.com]\n", encoding="utf-8")
    funcs = build_gateway_worker_functions(
        control_plane_url="http://cp:8001",
        resolve_cache_ttl_s=60.0,
        upstream_default_timeout_s=10.0,
        upstream_max_response_bytes=1024,
        external_allowlist_path=str(allowlist),
    )
    names = sorted(f.name for f in funcs)
    assert names == ["call_external", "call_internal", "invoke_app_function"]
    for f in funcs:
        assert f.handler is not None
        assert f.input_schema
        assert f.output_schema
