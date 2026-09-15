"""invoke_app_function addressed by (app, function) name instead of function_id.

A function id is minted per-environment, so a node that pins one cannot be
promoted dev → qa → prod unchanged. These tests pin the name selector end to end:
the value object's accept/reject rules, the resolve client's request shape and
cache keying, and the handler actually taking the name branch.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.layer1_domain.value_objects.app_function_request import InvokeAppFunctionParams
from app.layer1_domain.value_objects.gateway_request import InvalidParams
from app.layer4_frameworks.registry_client import (
    AppFunctionResolveClient,
    ServiceNotFound,
)
from app.functions import _make_invoke_app_function_handler


# ── the selector rules ────────────────────────────────────────────────

@pytest.mark.unit
def test_name_pair_is_accepted_and_flagged_by_name():
    p = InvokeAppFunctionParams.from_dict(
        {"app": "workflow-api", "function": "get_expression_reference", "args": {"q": 1}}
    )
    assert (p.app, p.function) == ("workflow-api", "get_expression_reference")
    assert p.function_id == ""
    assert p.by_name is True
    assert p.args == {"q": 1}


@pytest.mark.unit
def test_function_id_still_wins_its_own_branch():
    p = InvokeAppFunctionParams.from_dict({"function_id": "abc"})
    assert p.by_name is False
    assert (p.app, p.function) == ("", "")


@pytest.mark.unit
def test_name_pair_is_trimmed():
    p = InvokeAppFunctionParams.from_dict({"app": "  gov  ", "function": "  get_order  "})
    assert (p.app, p.function) == ("gov", "get_order")


@pytest.mark.unit
def test_both_selectors_is_rejected_rather_than_silently_preferred():
    """They could disagree; a precedence rule would hide which one actually ran."""
    with pytest.raises(InvalidParams, match="not both"):
        InvokeAppFunctionParams.from_dict({"function_id": "abc", "app": "gov", "function": "f"})


@pytest.mark.unit
@pytest.mark.parametrize("raw", [
    {},                        # nothing
    {"app": "gov"},            # app without function
    {"function": "get_order"}, # function without app
    {"app": "  ", "function": "  "},  # blank after trim
])
def test_incomplete_selector_is_rejected(raw):
    with pytest.raises(InvalidParams):
        InvokeAppFunctionParams.from_dict(raw)


@pytest.mark.unit
def test_non_string_name_is_rejected():
    with pytest.raises(InvalidParams, match="`app` must be a string"):
        InvokeAppFunctionParams.from_dict({"app": 7, "function": "f"})


# ── the resolve client ────────────────────────────────────────────────

class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.call_count = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        await request.aread()
        self.requests.append(request)
        return self._handler(request, self.call_count)


def _ok(**over):
    data = {
        "kind": "internal",
        "base_url": "https://gov.internal",
        "allowed_hosts": [],
        "method": "GET",
        "path_template": "/orders/{order_id}",
        "params": [{"key": "order_id", "location": "path", "required": True}],
        "timeout_ms": 2500,
        "auth_header_name": "Authorization",
        "auth_header_value": "Bearer t",
    }
    data.update(over)
    return httpx.Response(
        200,
        content=json.dumps({"status": "ok", "data": data}).encode(),
        headers={"content-type": "application/json"},
    )


def _client(transport):
    return AppFunctionResolveClient(
        control_plane_url="http://cp:8001/",
        ttl_s=60.0,
        http_client_factory=lambda: httpx.AsyncClient(transport=transport, timeout=5.0),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_by_name_posts_the_pair_to_the_name_route():
    transport = _MockTransport(lambda req, n: _ok())
    resolved = await _client(transport).resolve_by_name("gov", "get_order")

    req = transport.requests[0]
    assert str(req.url) == "http://cp:8001/api/v1/gateway/functions/_resolve"
    assert json.loads(req.content) == {"app": "gov", "function": "get_order"}
    assert resolved.path_template == "/orders/{order_id}"
    assert resolved.auth_header_value == "Bearer t"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_by_name_caches():
    transport = _MockTransport(lambda req, n: _ok())
    client = _client(transport)
    await client.resolve_by_name("gov", "get_order")
    await client.resolve_by_name("gov", "get_order")
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_name_and_id_are_separate_cache_slots():
    """A bare-string cache key would let an id 'gov' collide with an app named 'gov'."""
    transport = _MockTransport(lambda req, n: _ok())
    client = _client(transport)
    await client.resolve("gov")                    # by id
    await client.resolve_by_name("gov", "gov")     # by name
    assert transport.call_count == 2
    assert str(transport.requests[0].url).endswith("/functions/gov/_resolve")
    assert str(transport.requests[1].url).endswith("/functions/_resolve")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_by_name_404_raises_not_found_and_negative_caches():
    transport = _MockTransport(lambda req, n: httpx.Response(404))
    client = _client(transport)
    with pytest.raises(ServiceNotFound, match="get_order"):
        await client.resolve_by_name("gov", "get_order")
    with pytest.raises(ServiceNotFound, match="cached"):
        await client.resolve_by_name("gov", "get_order")
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidate_by_name_drops_the_entry():
    transport = _MockTransport(lambda req, n: _ok())
    client = _client(transport)
    await client.resolve_by_name("gov", "get_order")
    client.invalidate_by_name("gov", "get_order")
    await client.resolve_by_name("gov", "get_order")
    assert transport.call_count == 2


# ── the handler takes the name branch ─────────────────────────────────

class _FakeResolver:
    def __init__(self, resolved):
        self._resolved = resolved
        self.by_name_calls: list[tuple[str, str]] = []
        self.by_id_calls: list[str] = []

    async def resolve(self, function_id):
        self.by_id_calls.append(function_id)
        return self._resolved

    async def resolve_by_name(self, app, function):
        self.by_name_calls.append((app, function))
        return self._resolved


class _FakeUpstream:
    def __init__(self):
        self.sent: dict | None = None

    async def send(self, *, method, url, headers, body, timeout_ms):
        self.sent = {"method": method, "url": url, "headers": headers, "body": body}
        return type("R", (), {"status_code": 200, "headers": {}, "body": {"ok": True}})()


def _resolved_spec():
    from app.layer4_frameworks.registry_client import ResolvedAppFunction
    return ResolvedAppFunction(
        kind="internal",
        base_url="https://gov.internal",
        allowed_hosts=(),
        method="GET",
        path_template="/orders/{order_id}",
        params=({"key": "order_id", "location": "path", "required": True},),
        timeout_ms=2500,
        auth_header_name="Authorization",
        auth_header_value="Bearer t",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handler_resolves_by_name_and_proxies():
    resolver, upstream = _FakeResolver(_resolved_spec()), _FakeUpstream()
    handler = _make_invoke_app_function_handler(resolver, upstream)

    result = await handler({"app": "gov", "function": "get_order", "args": {"order_id": "42"}})

    assert resolver.by_name_calls == [("gov", "get_order")]
    assert resolver.by_id_calls == []
    assert upstream.sent["url"] == "https://gov.internal/orders/42"
    assert upstream.sent["headers"]["Authorization"] == "Bearer t"
    assert result["status_code"] == 200
    assert "error" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handler_still_resolves_by_id():
    resolver, upstream = _FakeResolver(_resolved_spec()), _FakeUpstream()
    handler = _make_invoke_app_function_handler(resolver, upstream)

    await handler({"function_id": "fn-1", "args": {"order_id": "42"}})

    assert resolver.by_id_calls == ["fn-1"]
    assert resolver.by_name_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handler_rejects_a_half_supplied_name_pair_as_bad_request():
    resolver, upstream = _FakeResolver(_resolved_spec()), _FakeUpstream()
    handler = _make_invoke_app_function_handler(resolver, upstream)

    result = await handler({"app": "gov", "args": {}})   # no `function`

    assert result["error"]["code"] == "BAD_REQUEST"
    assert upstream.sent is None       # nothing was proxied
    assert resolver.by_name_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handler_maps_unknown_name_to_function_not_found():
    class _Missing(_FakeResolver):
        async def resolve_by_name(self, app, function):
            raise ServiceNotFound(f"function {function!r} of app {app!r} not found")

    upstream = _FakeUpstream()
    handler = _make_invoke_app_function_handler(_Missing(_resolved_spec()), upstream)

    result = await handler({"app": "gov", "function": "nope"})

    assert result["error"]["code"] == "FUNCTION_NOT_FOUND"
    assert upstream.sent is None
