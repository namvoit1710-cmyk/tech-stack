"""Unit tests for invoke_app_function: the request builder + the handler.

No network: a fake resolve client + fake upstream cover the happy path; the
external allowlist reject is checked before any DNS lookup.
"""
from __future__ import annotations

import pytest

from app.functions import _make_invoke_app_function_handler
from app.layer1_domain.value_objects.app_function_request import (
    InvokeAppFunctionParams,
    build_upstream_request,
)
from app.layer1_domain.value_objects.gateway_request import InvalidParams
from app.layer4_frameworks.registry_client import (
    RegistryUnavailable,
    ResolvedAppFunction,
    ServiceNotFound,
)
from app.layer4_frameworks.upstream_http import UpstreamResponse


# ── request builder ──────────────────────────────────────────────────

def _internal(**over) -> ResolvedAppFunction:
    base = dict(
        kind="internal", base_url="https://gov.internal", allowed_hosts=(),
        method="GET", path_template="/orders/{order_id}",
        params=({"key": "order_id", "location": "path", "required": True},
                {"key": "q", "location": "query", "required": False}),
        timeout_ms=0, auth_header_name="Authorization", auth_header_value="Bearer sek",
    )
    base.update(over)
    return ResolvedAppFunction(**base)


def test_build_request_internal_path_query_auth():
    b = build_upstream_request(
        _internal(),
        InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {"order_id": "42", "q": "x"}}),
    )
    assert b.url == "https://gov.internal/orders/42?q=x"
    assert b.headers["Authorization"] == "Bearer sek"


def test_build_request_missing_required_path_param():
    with pytest.raises(InvalidParams):
        build_upstream_request(
            _internal(),
            InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {}}),
        )


def test_build_request_external_absolute_url_and_body():
    r = ResolvedAppFunction(
        kind="external", base_url="", allowed_hosts=("api.partner.com",),
        method="POST", path_template="https://api.partner.com/v1/orders",
        params=({"key": "payload", "location": "body", "required": True},),
        timeout_ms=0, auth_header_name="", auth_header_value="",
    )
    b = build_upstream_request(
        r, InvokeAppFunctionParams.from_dict({"function_id": "f", "args": {"payload": {"x": 1}}}),
    )
    assert b.url == "https://api.partner.com/v1/orders" and b.body == {"x": 1}


# ── handler (fakes) ──────────────────────────────────────────────────

class _FakeResolve:
    def __init__(self, resolved=None, exc=None):
        self._resolved, self._exc = resolved, exc
    async def resolve(self, function_id):
        if self._exc:
            raise self._exc
        return self._resolved


class _FakeUpstream:
    def __init__(self):
        self.sent = None
    async def send(self, method, url, headers, body, timeout_ms):
        self.sent = {"method": method, "url": url, "headers": headers, "body": body, "timeout_ms": timeout_ms}
        return UpstreamResponse(status_code=200, headers={}, body={"ok": True})


@pytest.mark.asyncio
async def test_handler_internal_happy_path():
    up = _FakeUpstream()
    handler = _make_invoke_app_function_handler(_FakeResolve(_internal()), up)
    out = await handler({"function_id": "f1", "args": {"order_id": "42"}})
    assert out["status_code"] == 200 and out["body"] == {"ok": True}
    assert up.sent["url"] == "https://gov.internal/orders/42"
    assert up.sent["headers"]["Authorization"] == "Bearer sek"


@pytest.mark.asyncio
async def test_handler_function_not_found():
    handler = _make_invoke_app_function_handler(
        _FakeResolve(exc=ServiceNotFound("nope")), _FakeUpstream()
    )
    out = await handler({"function_id": "missing", "args": {}})
    assert out["error"]["code"] == "FUNCTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_handler_external_host_not_allowed():
    # Empty allowlist → the SSRF policy rejects before any DNS lookup.
    resolved = ResolvedAppFunction(
        kind="external", base_url="", allowed_hosts=(),
        method="GET", path_template="https://evil.example.com/x",
        params=(), timeout_ms=0, auth_header_name="", auth_header_value="",
    )
    handler = _make_invoke_app_function_handler(_FakeResolve(resolved), _FakeUpstream())
    out = await handler({"function_id": "f", "args": {}})
    assert out["error"]["code"] == "HOST_NOT_ALLOWED"


# ── P4: timeout precedence + scheme wiring ────────────────────────────

@pytest.mark.asyncio
async def test_handler_uses_function_timeout_when_caller_omits():
    up = _FakeUpstream()
    handler = _make_invoke_app_function_handler(_FakeResolve(_internal(timeout_ms=4500)), up)
    await handler({"function_id": "f", "args": {"order_id": "1"}})  # no timeout_ms
    assert up.sent["timeout_ms"] == 4500  # function's configured timeout applied


@pytest.mark.asyncio
async def test_handler_caller_timeout_overrides_function_timeout():
    up = _FakeUpstream()
    handler = _make_invoke_app_function_handler(_FakeResolve(_internal(timeout_ms=4500)), up)
    await handler({"function_id": "f", "args": {"order_id": "1"}, "timeout_ms": 1000})
    assert up.sent["timeout_ms"] == 1000  # caller override wins


@pytest.mark.asyncio
async def test_handler_external_scheme_not_allowed_when_https_only():
    # allowed_schemes defaults to ("https",) → an http target is rejected.
    resolved = ResolvedAppFunction(
        kind="external", base_url="", allowed_hosts=("api.partner.com",),
        method="GET", path_template="http://api.partner.com/x",
        params=(), timeout_ms=0, auth_header_name="", auth_header_value="",
    )
    handler = _make_invoke_app_function_handler(_FakeResolve(resolved), _FakeUpstream())
    out = await handler({"function_id": "f", "args": {}})
    assert out["error"]["code"] == "SCHEME_NOT_ALLOWED"
