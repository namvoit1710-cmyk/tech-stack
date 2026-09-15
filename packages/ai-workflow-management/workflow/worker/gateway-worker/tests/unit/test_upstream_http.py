"""Unit tests for UpstreamHttpClient (httpx wrapper)."""
from __future__ import annotations

import httpx
import pytest

from app.layer4_frameworks.upstream_http import (
    ResponseTooLarge,
    UpstreamError,
    UpstreamHttpClient,
    UpstreamTimeout,
)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request):
        return self._handler(request)


def _factory(transport):
    def _f(timeout_s):
        return httpx.AsyncClient(transport=transport, timeout=timeout_s)
    return _f


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_parses_json_response():
    def handler(req):
        return httpx.Response(
            200,
            content=b'{"x": 1}',
            headers={"content-type": "application/json"},
        )
    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=_factory(_MockTransport(handler)),
    )
    response = await client.send("GET", "https://x", {}, None, None)
    assert response.status_code == 200
    assert response.body == {"x": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_returns_text_for_non_json():
    def handler(req):
        return httpx.Response(
            200,
            content=b"hello world",
            headers={"content-type": "text/plain"},
        )
    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=_factory(_MockTransport(handler)),
    )
    response = await client.send("GET", "https://x", {}, None, None)
    assert response.body == "hello world"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_raises_response_too_large():
    def handler(req):
        return httpx.Response(200, content=b"a" * 2048)
    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=_factory(_MockTransport(handler)),
    )
    with pytest.raises(ResponseTooLarge):
        await client.send("GET", "https://x", {}, None, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_maps_timeout():
    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ReadTimeout("slow")
    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=_factory(TimeoutTransport()),
    )
    with pytest.raises(UpstreamTimeout):
        await client.send("GET", "https://x", {}, None, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_maps_connect_error():
    class BadTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("nope")
    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=_factory(BadTransport()),
    )
    with pytest.raises(UpstreamError):
        await client.send("GET", "https://x", {}, None, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_caps_timeout_to_default():
    captured = {}

    def factory(timeout_s):
        captured["timeout"] = timeout_s
        return httpx.AsyncClient(
            transport=_MockTransport(lambda r: httpx.Response(200, content=b"x")),
            timeout=timeout_s,
        )

    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=factory,
    )
    # 999s requested; default cap is 10s
    await client.send("GET", "https://x", {}, None, 999_000)
    assert captured["timeout"] == 10.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_uses_caller_timeout_when_below_default():
    captured = {}

    def factory(timeout_s):
        captured["timeout"] = timeout_s
        return httpx.AsyncClient(
            transport=_MockTransport(lambda r: httpx.Response(200, content=b"x")),
            timeout=timeout_s,
        )

    client = UpstreamHttpClient(
        default_timeout_s=10.0,
        max_response_bytes=1024,
        http_client_factory=factory,
    )
    await client.send("GET", "https://x", {}, None, 3_000)  # 3s
    assert captured["timeout"] == 3.0
