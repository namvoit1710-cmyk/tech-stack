"""SA-1905 C2 — worker-side StreamingOutputConverter.

The worker converts a large list-of-dicts output to a streamed CSV (native
producer), uploads it to the file service, and returns a file_id file_ref (same
shape the CP produces) instead of the inline payload. Verified with a fake
uploader — the CSV round-trips and the ref is CP-compatible.

Skipped when the native json_csv_streamer wheel isn't installed.
"""

from __future__ import annotations

import csv
import io

import pytest

pytest.importorskip("json_csv_streamer")

from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
    StreamingOutputConverter,
    FILE_REF_MARKER,
)


class _FakeUploader:
    def __init__(self, file_id="file-123"):
        self._file_id = file_id
        self.uploads = []

    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        self.uploads.append((filename, file_bytes, content_type, idempotency_key))
        return self._file_id


def _rows(n):
    return [{"id": i, "sku": f"S{i}", "qty": i % 4, "meta": {"w": i % 3}} for i in range(n)]


@pytest.mark.asyncio
async def test_convert_streams_and_returns_cp_compatible_file_ref():
    up = _FakeUploader()
    conv = StreamingOutputConverter(up, enabled=True, threshold_bytes=1)
    rows = _rows(30)

    ref = await conv.convert("task-1", "output", rows)

    assert ref is not None and ref[FILE_REF_MARKER] is True
    assert ref["file_id"] == "file-123"
    assert ref["row_count"] == 30
    assert ref["content_type"] == "text/csv"
    assert "__row_id" not in ref["columns"]
    assert ref["column_types"].get("qty") == "number"
    # SA-1977 — a worker-streamed ref DECLARES its root kind, exactly like a
    # control-plane-stored one. Without this the CP would have to guess the kind
    # of precisely the values that are too large to guess about.
    assert ref["value_kind"] == "array"
    assert ref["item_kind"] == "object"

    # Exactly one upload; the CSV round-trips + carries __row_id + a content key.
    assert len(up.uploads) == 1
    fname, csv_bytes, ct, idem = up.uploads[0]
    assert fname == "task-1-output.csv" and ct == "text/csv"
    assert idem.startswith("sha256:")
    reader = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert len(reader) == 30
    assert reader[0]["__row_id"] == "0" and reader[5]["sku"] == "S5" and reader[5]["meta.w"] == str(5 % 3)


@pytest.mark.asyncio
async def test_below_threshold_returns_none():
    conv = StreamingOutputConverter(_FakeUploader(), enabled=True, threshold_bytes=10 ** 9)
    assert await conv.convert("t", "o", _rows(3)) is None


@pytest.mark.asyncio
async def test_disabled_returns_none():
    conv = StreamingOutputConverter(_FakeUploader(), enabled=False, threshold_bytes=1)
    assert await conv.convert("t", "o", _rows(1000)) is None


@pytest.mark.asyncio
async def test_upload_failure_falls_back_to_inline():
    # Uploader returns no file_id -> convert returns None -> caller keeps inline.
    conv = StreamingOutputConverter(_FakeUploader(file_id=""), enabled=True, threshold_bytes=1)
    assert await conv.convert("t", "o", _rows(50)) is None


@pytest.mark.asyncio
async def test_mkdtemp_failure_returns_none_not_raises(monkeypatch):
    # A failing/full TMPDIR must yield None (inline fallback), NOT raise — the
    # async execute path's single-type branch doesn't wrap this, so a raise
    # would kill the background task with no callback and hang the node.
    import tempfile
    conv = StreamingOutputConverter(_FakeUploader(), enabled=True, threshold_bytes=1)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: (_ for _ in ()).throw(OSError("no space left")))
    assert await conv.convert("t", "o", _rows(50)) is None


def test_should_stream_gating():
    conv = StreamingOutputConverter(_FakeUploader(), enabled=True, threshold_bytes=1)
    assert conv.should_stream(_rows(100)) is True
    assert conv.should_stream([]) is False
    assert conv.should_stream({}) is False
    # SA-1910 — a non-empty DICT now streams too, and this line used to assert
    # the opposite. That rule is what broke a live run: a code node returning
    # `{"title": …, "rows": [30 000 rows]}` is the shape that produces a nested
    # artifact tree, and because the gate saw a dict and declined, the whole
    # 2.3 MB was published on `workflow.results` and refused at the broker's
    # 1 048 576-byte cap. Size decides now, not type.
    assert conv.should_stream({"a": 1}) is True
    big = StreamingOutputConverter(_FakeUploader(), enabled=True, threshold_bytes=10**9)
    assert big.should_stream({"a": 1}) is False
    # SA-1977 — a list of SCALARS streams too, matching the control plane's
    # ``_should_stream``. A worker returning a 500 MB list[str] has exactly the
    # problem streaming exists to solve, and the native producer handles it (it
    # writes the reserved ``__value`` column). Two different gates would mean the
    # same output streams on one side of the wire and rides an event on the other.
    assert conv.should_stream(["x", "y"]) is True
