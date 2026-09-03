"""Phase 1, worker half — the codec reaches the crate, and `none` reaches it as NOTHING.

Same contract and same reasoning as the control plane's twin
(`workflow-control-plane-service/tests/unit/test_arrow_codec_plumbing.py`), and
it matters MORE here: worker-sdk ships in ten worker images that deploy
independently, so "new config, old wheel" is not a narrow window on this side —
it is the normal state for however long the fleet takes to roll.

If a worker passed ``codec="none"`` unconditionally to an old wheel it would
raise ``TypeError`` inside the sidecar's own ``try``, which degrades to CSV-only.
Nothing would go red; sidecars would just stop appearing.
"""

from __future__ import annotations

import json

import pytest

from worker_sdk.layer4_frameworks.providers.data_io import (
    streaming_output_converter as soc,
)
from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
    StreamingOutputConverter,
)


class _RecordingStreamer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def arrow_bytes_to_rows(self, buf, strict_parity=True):  # pragma: no cover
        return []

    def jsonl_to_csv_and_nested_arrow(self, jsonl_path, csv_path, arrow_path, *a, **kw):
        self.calls.append(("jsonl_to_csv_and_nested_arrow", a, kw))
        return self._write(jsonl_path, csv_path, arrow_path)

    def jsonl_to_nested_arrow(self, jsonl_path, arrow_path, *a, **kw):
        self.calls.append(("jsonl_to_nested_arrow", a, kw))
        with open(arrow_path, "wb") as fh:
            fh.write(b"ARROW1\x00\x00stub")
        return (1, {})

    def jsonl_split_recursive(self, jsonl_path, outdir, *a, **kw):
        import os
        rows = [json.loads(l) for l in open(jsonl_path, encoding="utf-8") if l.strip()]
        csv_path = os.path.join(outdir, "root.csv")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("__row_id,title\n0,t\n")
        # The manifest keys ``_upload_tree`` actually reads: csv_path,
        # content_key, children.
        return {
            "csv_path": csv_path, "content_key": "sha256:stub", "children": [],
            "row_count": len(rows), "columns": ["__row_id", "title"],
            "column_types": {"title": "string"}, "size_bytes": 16,
        }

    @staticmethod
    def _write(jsonl_path, csv_path, arrow_path):
        rows = [json.loads(l) for l in open(jsonl_path, encoding="utf-8") if l.strip()]
        cols = sorted({k for r in rows if isinstance(r, dict) for k in r})
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(["__row_id", *cols]) + "\n")
            for i, r in enumerate(rows):
                fh.write(",".join([str(i), *[str(r.get(c, "")) for c in cols]]) + "\n")
        with open(arrow_path, "wb") as fh:
            fh.write(b"ARROW1\x00\x00stub")
        return (cols, {c: "string" for c in cols}, len(rows), "object")

    def codec_args(self, fn: str) -> list:
        return [(a, kw) for name, a, kw in self.calls if name == fn]


class _Uploader:
    def __init__(self) -> None:
        self.n = 0

    async def upload(self, data, filename, content_type, idempotency_key=None):
        self.n += 1
        return f"file_{self.n:04d}"


@pytest.fixture
def streamer(monkeypatch):
    rec = _RecordingStreamer()
    monkeypatch.setattr(soc, "_STREAMER", rec)
    return rec


def _conv(**kw) -> StreamingOutputConverter:
    return StreamingOutputConverter(
        _Uploader(), enabled=True, threshold_bytes=1, arrow_sidecar=True, **kw,
    )


ROWS = [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]


@pytest.mark.asyncio
async def test_default_config_calls_the_crate_with_no_codec_argument(streamer):
    ref = await _conv().convert("t1", "out", ROWS)
    assert ref is not None, "the converter did not run — the test proves nothing"
    calls = streamer.codec_args("jsonl_to_csv_and_nested_arrow")
    assert calls
    for args, kwargs in calls:
        assert args == () and kwargs == {}, (
            "a worker on the OLD wheel would raise TypeError here and quietly "
            "stop writing sidecars"
        )


@pytest.mark.asyncio
async def test_explicit_none_is_also_passed_as_nothing(streamer):
    await _conv(arrow_codec="none").convert("t1", "out", ROWS)
    for args, kwargs in streamer.codec_args("jsonl_to_csv_and_nested_arrow"):
        assert args == () and kwargs == {}


@pytest.mark.asyncio
async def test_codec_reaches_the_crate(streamer):
    await _conv(arrow_codec="zstd").convert("t1", "out", ROWS)
    calls = streamer.codec_args("jsonl_to_csv_and_nested_arrow")
    assert calls
    for args, kwargs in calls:
        assert "zstd" in args or kwargs.get("codec") == "zstd"


@pytest.mark.asyncio
async def test_codec_is_normalised_before_it_leaves_python(streamer):
    await _conv(arrow_codec=" ZSTD ").convert("t1", "out", ROWS)
    for args, kwargs in streamer.codec_args("jsonl_to_csv_and_nested_arrow"):
        assert "zstd" in args or kwargs.get("codec") == "zstd"


@pytest.mark.asyncio
async def test_the_tree_sidecar_writer_carries_it_too(streamer):
    """The dict-root path writes its sidecar through a different call."""
    await _conv(arrow_codec="zstd").convert("t1", "out", {"title": "t", "rows": ROWS})
    calls = streamer.codec_args("jsonl_to_nested_arrow")
    assert calls, "the tree sidecar writer was never reached"
    for args, kwargs in calls:
        assert "zstd" in args or kwargs.get("codec") == "zstd"
