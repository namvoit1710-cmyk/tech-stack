"""Unit tests for InternalServiceRegistryClient (TTL cache + dedup + errors)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.layer4_frameworks.registry_client import (
    InternalServiceRegistryClient,
    RegistryUnavailable,
    ServiceNotFound,
)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        return self._handler(request, self.call_count)


def _make_client_factory(transport: _MockTransport):
    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)
    return factory


def _ok_response(base_url="https://gov.internal", auth_header="Authorization", auth_value="Bearer t"):
    import json
    body = json.dumps({
        "status": "ok",
        "data": {
            "base_url": base_url,
            "auth_header_name": auth_header,
            "auth_header_value": auth_value,
            "auth_type": "bearer",
        },
    }).encode()
    return httpx.Response(200, content=body, headers={"content-type": "application/json"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_happy_path_caches_response():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    resolved = await client.resolve("governance")
    assert resolved.base_url == "https://gov.internal"
    assert resolved.auth_header_name == "Authorization"
    # Second call within TTL → cached, no extra HTTP call
    resolved2 = await client.resolve("governance")
    assert resolved == resolved2
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_404_raises_service_not_found_and_caches_negative():
    def handler(req, n):
        body = b'{"status":"error","error":{"code":"GATEWAY_SERVICE_NOT_FOUND","message":"x"}}'
        return httpx.Response(404, content=body, headers={"content-type": "application/json"})

    transport = _MockTransport(handler)
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    with pytest.raises(ServiceNotFound):
        await client.resolve("ghost")
    # Cached negative — second call doesn't hit HTTP
    with pytest.raises(ServiceNotFound):
        await client.resolve("ghost")
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_500_raises_registry_unavailable_and_does_not_cache():
    def handler(req, n):
        return httpx.Response(500, content=b'{"status":"error"}')

    transport = _MockTransport(handler)
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    with pytest.raises(RegistryUnavailable):
        await client.resolve("governance")
    with pytest.raises(RegistryUnavailable):
        await client.resolve("governance")
    assert transport.call_count == 2  # not cached


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_network_error_raises_registry_unavailable():
    class Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("network down")

    def factory():
        return httpx.AsyncClient(transport=Boom(), timeout=5.0)

    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=factory,
    )
    with pytest.raises(RegistryUnavailable, match="network down"):
        await client.resolve("governance")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_dedupes_concurrent_misses():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    # Fire 5 concurrent resolves on the same name — only ONE should hit HTTP.
    results = await asyncio.gather(*[client.resolve("governance") for _ in range(5)])
    assert len({r.base_url for r in results}) == 1
    assert transport.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidate_drops_cache_entry():
    transport = _MockTransport(lambda req, n: _ok_response())
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    await client.resolve("governance")
    client.invalidate("governance")
    await client.resolve("governance")
    assert transport.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_missing_base_url_treated_as_registry_error():
    def handler(req, n):
        import json
        body = json.dumps({"status": "ok", "data": {}}).encode()
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    transport = _MockTransport(handler)
    client = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=_make_client_factory(transport),
    )
    with pytest.raises(RegistryUnavailable, match="base_url"):
        await client.resolve("malformed")
