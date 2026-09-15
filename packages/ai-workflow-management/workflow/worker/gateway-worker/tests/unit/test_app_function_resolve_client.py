"""Unit tests for AppFunctionResolveClient (function-id _resolve + TTL cache + errors).

Mirrors the InternalServiceRegistryClient tests but for the P2 function-resolve
path (``/api/v1/gateway/functions/{id}/_resolve``). HTTP is mocked at the
httpx.AsyncClient seam via an injected transport factory — no real network.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.layer4_frameworks.registry_client import (
    AppFunctionResolveClient,
    RegistryUnavailable,
    ServiceNotFound,
)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.call_count = 0
        self.last_url: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        self.last_url = str(request.url)
        return self._handler(request, self.call_count)


def _make_client_factory(transport: _MockTransport):
    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)
    return factory


def _ok_response(**over):
    data = {
        "kind": "internal",
        "base_url": "https://gov.internal",
        "allowed_hosts": [],
        "method": "GET",
        "path_template": "/orders/{id}",
        "params": [{"key": "id", "location": "path", "required": True}],
        "timeout_ms": 2500,
        "auth_header_name": "Authorization",
        "auth_header_value": "Bearer t",
    }
    data.update(over)
    body = json.dumps({"status": "ok", "data": data}).encode()
    return httpx.Response(200, content=body, headers={"content-type": "application/json"})


def _client(transport):
    return AppFunctionResolveClient(
        control_plane_url="http://cp:8001/",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )


# ── happy path ────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_happy_path_maps_all_fields_and_caches():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = _client(transport)
    resolved = await client.resolve("get_order")
    assert resolved.kind == "internal"
    assert resolved.base_url == "https://gov.internal"
    assert resolved.method == "GET"
    assert resolved.path_template == "/orders/{id}"
    assert resolved.timeout_ms == 2500
    assert resolved.params == ({"key": "id", "location": "path", "required": True},)
    # rstrip('/') on control_plane_url applied, correct endpoint used
    assert transport.last_url == "http://cp:8001/api/v1/gateway/functions/get_order/_resolve"
    # Second call within TTL is cached
    again = await client.resolve("get_order")
    assert again == resolved
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_external_kind_reads_allowed_hosts_tuple():
    transport = _MockTransport(
        lambda req, n: _ok_response(
            kind="external", base_url="", allowed_hosts=["api.partner.com", "*.crm.example.com"],
            path_template="https://api.partner.com/v1",
        )
    )
    resolved = await _client(transport).resolve("call_partner")
    assert resolved.kind == "external"
    assert resolved.allowed_hosts == ("api.partner.com", "*.crm.example.com")


# ── failure path: HTTP status mapping ───────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_404_raises_function_not_found_and_caches_negative():
    def handler(req, n):
        return httpx.Response(404, content=b'{"status":"error"}')
    transport = _MockTransport(handler)
    client = _client(transport)
    with pytest.raises(ServiceNotFound):
        await client.resolve("ghost")
    # Negative cache: second call does not re-hit HTTP.
    with pytest.raises(ServiceNotFound):
        await client.resolve("ghost")
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_500_raises_registry_unavailable_and_does_not_cache():
    transport = _MockTransport(lambda req, n: httpx.Response(503, content=b"{}"))
    client = _client(transport)
    with pytest.raises(RegistryUnavailable):
        await client.resolve("f")
    with pytest.raises(RegistryUnavailable):
        await client.resolve("f")
    assert transport.call_count == 2  # not cached


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_network_error_raises_registry_unavailable():
    class Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("cp down")

    def factory():
        return httpx.AsyncClient(transport=Boom(), timeout=5.0)

    client = AppFunctionResolveClient(
        control_plane_url="http://cp:8001", ttl_s=60.0, http_client_factory=factory,
    )
    with pytest.raises(RegistryUnavailable, match="cp down"):
        await client.resolve("f")


# ── failure path: envelope / payload validation ────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_invalid_json_raises_registry_unavailable():
    transport = _MockTransport(
        lambda req, n: httpx.Response(
            200, content=b"not json", headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(RegistryUnavailable, match="invalid JSON"):
        await _client(transport).resolve("f")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_envelope_not_ok_raises_registry_unavailable():
    body = json.dumps({"status": "error", "error": "boom"}).encode()
    transport = _MockTransport(
        lambda req, n: httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    with pytest.raises(RegistryUnavailable, match="not ok"):
        await _client(transport).resolve("f")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_missing_path_template_treated_as_registry_error():
    # Boundary: empty path_template is the sentinel that fails validation.
    transport = _MockTransport(lambda req, n: _ok_response(path_template=""))
    with pytest.raises(RegistryUnavailable, match="path_template"):
        await _client(transport).resolve("f")


# ── boundary: timeout_ms coercion + defaults ────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_defaults_when_optional_fields_absent():
    # data has only path_template — method defaults to GET, timeout_ms to 0.
    data = {"path_template": "/x"}
    body = json.dumps({"status": "ok", "data": data}).encode()
    transport = _MockTransport(
        lambda req, n: httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    resolved = await _client(transport).resolve("f")
    assert resolved.method == "GET"
    assert resolved.timeout_ms == 0
    assert resolved.allowed_hosts == ()
    assert resolved.params == ()


# ── cache management ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidate_drops_cache_entry():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = _client(transport)
    await client.resolve("f")
    client.invalidate("f")
    await client.resolve("f")
    assert transport.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_dedupes_concurrent_misses():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = _client(transport)
    results = await asyncio.gather(*[client.resolve("f") for _ in range(5)])
    assert len({r.base_url for r in results}) == 1
    assert transport.call_count == 1  # per-key lock dedupes cold-start fan-out
