"""The executor's file-service client — the third outbound dependency.

Only used on the offload path (a result too large to publish). It is sync on
purpose: the use case that calls it is sync, and it runs after a failure, so an
event-loop hop would buy nothing.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.layer4_frameworks.providers.http_file_uploader import HttpFileUploader


def _uploader(handler, **kw):
    return HttpFileUploader(
        "http://fs:8000", transport=httpx.MockTransport(handler), **kw,
    )


def test_it_posts_a_multipart_file_and_returns_the_file_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"file_id": "file-123"})

    got = _uploader(handler).upload(b'{"a": 1}', "t1-output.json", "application/json")

    assert got == "file-123"
    assert seen["url"].startswith("http://fs:8000/api/v1/upload")
    assert "filename=t1-output.json" in seen["url"]
    # multipart, carrying both the filename and the declared content type
    assert b"t1-output.json" in seen["body"]
    assert b"application/json" in seen["body"]
    assert b'{"a": 1}' in seen["body"]


def test_a_200_with_no_file_id_returns_empty_rather_than_inventing_one():
    """A pointer to nowhere is worse than no pointer: the caller must degrade."""
    got = _uploader(lambda r: httpx.Response(200, json={})).upload(b"{}", "f.json")

    assert got == ""


def test_an_http_error_raises_so_the_caller_can_degrade():
    with pytest.raises(httpx.HTTPStatusError):
        _uploader(lambda r: httpx.Response(500, text="boom")).upload(b"{}", "f.json")


def test_the_idempotency_key_is_sent_only_when_given():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("X-Idempotency-Key")
        return httpx.Response(200, json={"file_id": "f1"})

    _uploader(handler).upload(b"{}", "f.json")
    assert seen["key"] is None

    _uploader(handler).upload(b"{}", "f.json", idempotency_key="abc")
    assert seen["key"] == "abc"


def test_a_trailing_slash_in_the_base_url_does_not_double_up():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"file_id": "f1"})

    HttpFileUploader(
        "http://fs:8000/", transport=httpx.MockTransport(handler),
    ).upload(b"{}", "f.json")

    assert "//api/v1" not in seen["url"]
    assert json.loads('"ok"') == "ok"  # sanity: the module imports cleanly
