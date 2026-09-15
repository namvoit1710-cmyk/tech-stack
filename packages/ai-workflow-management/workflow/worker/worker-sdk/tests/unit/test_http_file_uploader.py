"""SA-1905 C2 — worker HttpFileUploader (file-service upload client).

Matches the control-plane upload contract: POST /api/v1/upload?filename=…,
multipart ("file" part), X-Idempotency-Key header, returns {file_id}. Uses
httpx.MockTransport so no real network / file service is needed.
"""

from __future__ import annotations

import httpx
import pytest

from worker_sdk.layer4_frameworks.providers.file_service.http_file_uploader import (
    HttpFileUploader,
    content_idempotency_key,
)


@pytest.mark.asyncio
async def test_upload_posts_multipart_and_returns_file_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["idem"] = request.headers.get("X-Idempotency-Key")
        seen["ct"] = request.headers.get("content-type", "")
        seen["has_file_part"] = b'name="file"' in request.content
        return httpx.Response(200, json={"file_id": "fid-9", "status": "ok"})

    up = HttpFileUploader("https://fs.example.com", transport=httpx.MockTransport(handler))
    fid = await up.upload(b"__row_id,a\r\n0,1\r\n", "t.csv", "text/csv", idempotency_key="sha256:abc")

    assert fid == "fid-9"
    assert seen["url"].endswith("/api/v1/upload?filename=t.csv")
    assert seen["idem"] == "sha256:abc"
    assert "multipart/form-data" in seen["ct"]
    assert seen["has_file_part"] is True


@pytest.mark.asyncio
async def test_upload_no_idempotency_header_when_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Idempotency-Key") is None
        return httpx.Response(200, json={"file_id": "f"})

    up = HttpFileUploader("https://fs.example.com/", transport=httpx.MockTransport(handler))
    assert await up.upload(b"x", "t.csv") == "f"


@pytest.mark.asyncio
async def test_upload_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    up = HttpFileUploader("https://fs.example.com", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await up.upload(b"x", "t.csv")


def test_content_idempotency_key_is_stable_and_prefixed():
    assert content_idempotency_key(b"abc") == content_idempotency_key(b"abc")
    assert content_idempotency_key(b"abc") != content_idempotency_key(b"abd")
    assert content_idempotency_key(b"abc").startswith("sha256:")
