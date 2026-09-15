"""Regression: HttpFileServiceClient.upload_file must send multipart/form-data.

The file service rejects a raw-body POST with HTTP 422
``{"loc":["body","file"],"msg":"Field required"}`` — it requires a
``multipart/form-data`` request with a ``file`` part (FastAPI
``file: UploadFile = File(...)``). These tests capture the outgoing request
via ``httpx.MockTransport`` and pin that contract so a regression to raw-body
upload (which silently yields ``file_id=""`` and an empty, COMPLETED run) is
caught here.
"""

from __future__ import annotations

import httpx
import pytest

from app.layer4_frameworks.clients.http_file_service_client import (
    HttpFileServiceClient,
    _MAX_UPLOAD_FILENAME_LEN,
    _UPLOAD_FILENAME_HASH_HEX,
    _unique_csv_filename,
)


def _sent_filename(captured: dict) -> str:
    """Extract the multipart ``file`` part's filename from a captured body."""
    body = captured["body"]
    text = body.decode("latin-1") if isinstance(body, bytes) else str(body)
    # ``Content-Disposition: form-data; name="file"; filename="<x>"``
    marker = 'filename="'
    i = text.index(marker) + len(marker)
    return text[i : text.index('"', i)]


# ── filename uniquification (file-service OData filename-collision mitigation) ──
# The deployed file service's OData read keys by *filename*: two stored files
# sharing a name return the first's rows. The CP uploads node artifacts under
# logical, non-unique names, so re-runs collided and GET /data previewed the
# OLD value. We fold a content hash into CSV filenames to disambiguate.


def test_unique_csv_filename_is_content_addressable():
    a = _unique_csv_filename("calc.csv", b"x,y\n1,2\n")
    a_again = _unique_csv_filename("calc.csv", b"x,y\n1,2\n")
    b = _unique_csv_filename("calc.csv", b"x,y\n3,4\n")
    assert a == a_again                      # same content → same name (idempotent)
    assert a != b                            # different content → different name
    assert a.startswith("calc-") and a.endswith(".csv")  # stem + ext preserved


def test_unique_csv_filename_without_extension():
    import hashlib
    out = _unique_csv_filename("noext", b"data")
    # Short (16-hex) content tag, no extension — same [:16] convention as event ids.
    assert out == f"noext-{hashlib.sha256(b'data').hexdigest()[:_UPLOAD_FILENAME_HASH_HEX]}"


def test_unique_csv_filename_uses_short_hash():
    """The folded hash is a 16-hex prefix, not the full 64-char digest (48 chars
    shorter) — the reason a name that used to overflow now fits."""
    out = _unique_csv_filename("calc.csv", b"x,y\n1,2\n")
    tag = out[len("calc-"):-len(".csv")]
    assert len(tag) == _UPLOAD_FILENAME_HASH_HEX == 16
    assert all(c in "0123456789abcdef" for c in tag)


def test_unique_csv_filename_is_length_bounded():
    """A pathologically long logical name (the live failure: ``{run}-{node}-input
    -body-<path>.csv`` with two 36-char UUIDs) must be trimmed to fit the file
    service's name limit — WITHOUT ever truncating the hash or the extension."""
    run, node = "9" * 36, "0" * 36  # two UUID-length segments, like the real key
    long_name = f"{run}-{node}-input-body-deepData.to_Plant.csv"
    assert len(long_name) > _MAX_UPLOAD_FILENAME_LEN  # would be rejected today
    out = _unique_csv_filename(long_name, b"__row_id,v\n0,1\n")
    assert len(out) <= _MAX_UPLOAD_FILENAME_LEN         # now fits
    assert out.endswith(".csv")                         # extension preserved
    # The full 16-hex hash survives intact right before the extension.
    import hashlib
    tag = hashlib.sha256(b"__row_id,v\n0,1\n").hexdigest()[:_UPLOAD_FILENAME_HASH_HEX]
    assert out.endswith(f"-{tag}.csv")


def test_unique_csv_filename_bounded_still_unique_and_idempotent():
    """Trimming the stem must not break the two invariants the name exists for:
    idempotent for identical content, distinct for different content — even when
    the (identical) trimmed stems coincide."""
    long_stem = "z" * 200  # forces a trim on every call
    a = _unique_csv_filename(f"{long_stem}.csv", b"content-A")
    a_again = _unique_csv_filename(f"{long_stem}.csv", b"content-A")
    b = _unique_csv_filename(f"{long_stem}.csv", b"content-B")
    assert a == a_again                       # same content → same name (dedup)
    assert a != b                             # different content → different name
    assert len(a) <= _MAX_UPLOAD_FILENAME_LEN and len(b) <= _MAX_UPLOAD_FILENAME_LEN


@pytest.mark.asyncio
async def test_upload_csv_same_logical_name_distinct_content_gets_distinct_filenames():
    """Two different CSVs uploaded under the same logical name must reach the
    file service with DIFFERENT filenames (the whole point of the mitigation)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"file_id": f"f-{len(seen)}"})

    fs = _client_with_handler(handler)
    cap: dict[str, object] = {}

    def grab(request: httpx.Request) -> httpx.Response:
        cap["body"] = request.content
        return httpx.Response(200, json={"file_id": "f"})

    fs1 = _client_with_handler(grab)
    await fs1.upload_file(b"__row_id,v\n1,RUN1\n", "root.csv", "text/csv")
    n1 = _sent_filename(cap)
    await fs1.upload_file(b"__row_id,v\n1,RUN2\n", "root.csv", "text/csv")
    n2 = _sent_filename(cap)
    assert n1 != n2, (n1, n2)
    assert n1.startswith("root-") and n2.startswith("root-")


@pytest.mark.asyncio
async def test_upload_non_csv_filename_is_not_rewritten():
    """Binary / user uploads (non-CSV content type) keep their original name so
    display names and Content-Disposition stay intact."""
    cap: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["body"] = request.content
        cap["url"] = str(request.url)
        return httpx.Response(200, json={"file_id": "f-pdf"})

    fs = _client_with_handler(handler)
    await fs.upload_file(b"%PDF-1.7 ...", "invoice.pdf", "application/pdf")
    assert _sent_filename(cap) == "invoice.pdf"
    assert "filename=invoice.pdf" in cap["url"]


def _client_with_handler(handler):
    """Build a HttpFileServiceClient whose internal httpx client routes through
    ``handler`` (a callable ``httpx.Request -> httpx.Response``)."""
    fs = HttpFileServiceClient("https://files.example.test")
    fs._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return fs


@pytest.mark.asyncio
async def test_upload_sends_multipart_with_file_part():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"file_id": "f-123", "status": "ok"})

    fs = _client_with_handler(handler)
    out = await fs.upload_file(b"col_a,col_b\n1,2\n", "node_root.csv", "text/csv")

    assert out.get("file_id") == "f-123"
    # The request must be multipart (httpx generates the boundary).
    assert captured["content_type"].startswith("multipart/form-data"), captured["content_type"]
    body = captured["body"]
    assert isinstance(body, bytes)
    # The required form field is named ``file`` and carries our filename + bytes.
    assert b'name="file"' in body
    # CSV filenames are disambiguated with a content hash (see
    # ``_unique_csv_filename``) so the file service's filename-keyed OData read
    # can't return a same-named sibling's stale rows. Stem + extension survive.
    assert b"node_root-" in body
    assert b".csv" in body
    assert b"col_a,col_b" in body
    # filename is also forwarded as a query param per the OpenAPI spec.
    assert "filename=node_root-" in captured["url"]


@pytest.mark.asyncio
async def test_upload_forwards_idempotency_key_header():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["idem"] = request.headers.get("x-idempotency-key", "")
        return httpx.Response(200, json={"file_id": "f-9"})

    fs = _client_with_handler(handler)
    await fs.upload_file(b"x\n", "a.csv", "text/csv", idempotency_key="key-42")

    assert captured["idem"] == "key-42"


@pytest.mark.asyncio
async def test_upload_422_missing_file_returns_empty_dict():
    """The original bug's exact server response — a 422 must surface as {} (so
    callers see file_id="") and not raise."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": [{"type": "missing", "loc": ["body", "file"],
                              "msg": "Field required", "input": None}]},
        )

    fs = _client_with_handler(handler)
    out = await fs.upload_file(b"x\n", "a.csv", "text/csv")
    assert out == {}

# ── download_file retry on transient errors (hardening) ──────────────────────
# A single transient blip (DNS hiccup, dropped keep-alive, 5xx) must not lose a
# download that feeds variable resolution. GET is idempotent → retry transient
# failures; 4xx (401/403/404) is permanent → fail fast.

@pytest.mark.asyncio
async def test_download_retries_transient_connect_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return httpx.Response(200, content=b"templateId\r\nabc\r\n",
                              headers={"content-type": "text/csv"})

    fs = _client_with_handler(handler)
    out = await fs.download_file("f-1")
    assert out == b"templateId\r\nabc\r\n"
    assert calls["n"] == 3  # two transient failures, recovered on the third


@pytest.mark.asyncio
async def test_download_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=b"ok", headers={"content-type": "text/csv"})

    fs = _client_with_handler(handler)
    out = await fs.download_file("f-1")
    assert out == b"ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_download_does_not_retry_404():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    fs = _client_with_handler(handler)
    out = await fs.download_file("missing")
    assert out is None
    assert calls["n"] == 1  # permanent — no retry
