"""End-to-end: call_internal wired with a real InternalServiceRegistryClient
+ a mock control-plane via httpx transport, no real network.

Verifies the worker correctly chains: parse params → call CP /_resolve →
inject auth → forward to upstream → return upstream response.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.functions import _make_call_internal_handler
from app.layer4_frameworks.registry_client import InternalServiceRegistryClient
from app.layer4_frameworks.upstream_http import UpstreamHttpClient


class _RoutingTransport(httpx.AsyncBaseTransport):
    """Routes requests by URL host so one transport can mock both CP and upstream."""

    def __init__(self):
        self.cp_calls: list[str] = []
        self.upstream_calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "cp":
            self.cp_calls.append(str(request.url))
            return _cp_resolve_response()
        if host == "gov.internal":
            self.upstream_calls.append(request)
            return httpx.Response(
                200,
                content=json.dumps({"id": 1, "status": "ok"}).encode(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404, content=b"")


def _cp_resolve_response() -> httpx.Response:
    body = json.dumps({
        "status": "ok",
        "data": {
            "base_url": "https://gov.internal",
            "auth_header_name": "Authorization",
            "auth_header_value": "Bearer secret-token",
            "auth_type": "bearer",
        },
    }).encode()
    return httpx.Response(200, content=body, headers={"content-type": "application/json"})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_call_internal_chains_resolve_then_upstream():
    transport = _RoutingTransport()

    def cp_factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    def upstream_factory(timeout_s):
        return httpx.AsyncClient(transport=transport, timeout=timeout_s)

    registry = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=cp_factory,
    )
    upstream = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024 * 1024,
        http_client_factory=upstream_factory,
    )
    handler = _make_call_internal_handler(registry, upstream)

    result = await handler({
        "service": "governance",
        "endpoint": "/tasks/123",
        "method": "GET",
    })

    assert result["status_code"] == 200
    assert result["body"] == {"id": 1, "status": "ok"}
    # CP was called exactly once for resolve
    assert len(transport.cp_calls) == 1
    assert "/api/v1/gateway/services/governance/_resolve" in transport.cp_calls[0]
    # Upstream got the registry-injected auth header
    upstream_req = transport.upstream_calls[0]
    assert upstream_req.headers.get("Authorization") == "Bearer secret-token"
    assert str(upstream_req.url) == "https://gov.internal/tasks/123"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_call_internal_caches_resolve_across_invocations():
    transport = _RoutingTransport()

    def cp_factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    def upstream_factory(timeout_s):
        return httpx.AsyncClient(transport=transport, timeout=timeout_s)

    registry = InternalServiceRegistryClient(
        control_plane_url="http://cp:8001",
        ttl_s=60.0,
        http_client_factory=cp_factory,
    )
    upstream = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024 * 1024,
        http_client_factory=upstream_factory,
    )
    handler = _make_call_internal_handler(registry, upstream)

    for _ in range(3):
        await handler({"service": "governance", "endpoint": "/x"})

    # Resolve happens once; three upstream calls
    assert len(transport.cp_calls) == 1
    assert len(transport.upstream_calls) == 3
