"""Phase 1 — the codec reaches the crate, and `none` reaches it as NOTHING.

The subtle requirement is the second half. The rollout ships the new wheel to 11
images *before* any writer is flipped
(`solace/specs/wcp-arrow-size-reduction-plan.md` §1.6), which means for a window
the control plane may hold a NEW config and an OLD wheel — one whose
``jsonl_to_csv_and_nested_arrow`` takes no ``codec`` argument at all.

If the default config passed ``codec="none"`` unconditionally, that call would
raise ``TypeError`` on the old wheel. Both call sites catch broadly and degrade
to CSV-only, so nothing would fail loudly — the sidecar would simply stop being
written, everywhere, with a green dashboard. That is the exact failure shape
this codebase keeps getting bitten by, so it is pinned here instead of hoped for:

    codec unset  ->  call the crate with the SAME arguments as today.
    codec set    ->  pass it.

These tests stub ``_STREAMER``; they need no wheel.
"""

from __future__ import annotations

import json

import pytest

from app.layer2_application.data import data_store_service as dss
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class _RecordingStreamer:
    """Every crate entry point the sidecar uses, recording how it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    # the reader — presence of this is what ``_arrow_available`` checks
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

    def jsonl_split_recursive(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _write(jsonl_path, csv_path, arrow_path):
        rows = [json.loads(line) for line in open(jsonl_path, encoding="utf-8") if line.strip()]
        cols = sorted({k for r in rows if isinstance(r, dict) for k in r})
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(["__row_id", *cols]) + "\n")
            for i, r in enumerate(rows):
                fh.write(",".join([str(i), *[str(r.get(c, "")) for c in cols]]) + "\n")
        with open(arrow_path, "wb") as fh:
            fh.write(b"ARROW1\x00\x00stub")
        return (cols, {c: "string" for c in cols}, len(rows), "object")

    def codec_args(self, fn: str) -> list:
        """Everything passed BEYOND the paths, per call to *fn*."""
        return [(a, kw) for name, a, kw in self.calls if name == fn]


@pytest.fixture
def streamer(monkeypatch):
    rec = _RecordingStreamer()
    monkeypatch.setattr(dss, "_STREAMER", rec)
    return rec


def _store(*, recursive_split: bool = True, **kw) -> DataStoreService:
    """``recursive_split`` picks WHICH writer runs, and both must carry the codec:

    * ``True``  -> ``_write_nested_sidecar``   -> ``jsonl_to_nested_arrow``
    * ``False`` -> ``_store_list_streaming``   -> ``jsonl_to_csv_and_nested_arrow``
    """
    return DataStoreService(
        MockFileServiceClient(),
        CsvConverter(),
        recursive_split=recursive_split,
        # ``_should_stream`` gates on this, not on the threshold alone.
        chunked_output_enabled=True,
        chunking_threshold_bytes=1,
        arrow_read_mode="off",
        arrow_min_bytes=0,
        arrow_write_enabled=True,
        arrow_strict_parity=True,
        **kw,
    )


ROWS = [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]
TREE = {"title": "t", "lines": ROWS}


# ---------------------------------------------------------------------------
# default: byte-for-byte the call the old wheel accepts
# ---------------------------------------------------------------------------

async def test_default_config_calls_the_crate_with_no_codec_argument(streamer):
    s = _store(recursive_split=False)
    await s.store_data("k", ROWS)
    calls = streamer.codec_args("jsonl_to_csv_and_nested_arrow")
    assert calls, "the sidecar writer was never reached — the test proves nothing"
    for args, kwargs in calls:
        assert args == () and kwargs == {}, (
            "default config passed a codec; on the OLD wheel this raises TypeError "
            "and the sidecar silently stops being written"
        )


async def test_explicit_none_is_also_passed_as_nothing(streamer):
    s = _store(recursive_split=False, arrow_codec="none")
    await s.store_data("k", ROWS)
    for args, kwargs in streamer.codec_args("jsonl_to_csv_and_nested_arrow"):
        assert args == () and kwargs == {}


# ---------------------------------------------------------------------------
# set: it reaches the crate, at BOTH call sites
# ---------------------------------------------------------------------------

async def test_codec_reaches_the_streaming_writer(streamer):
    s = _store(recursive_split=False, arrow_codec="zstd")
    await s.store_data("k", ROWS)
    calls = streamer.codec_args("jsonl_to_csv_and_nested_arrow")
    assert calls
    for args, kwargs in calls:
        assert "zstd" in args or kwargs.get("codec") == "zstd"


async def test_codec_reaches_the_nested_sidecar_writer(streamer):
    s = _store(arrow_codec="zstd")
    await s.store_data("k", TREE)
    calls = streamer.codec_args("jsonl_to_nested_arrow")
    assert calls, "the nested-sidecar writer was never reached"
    for args, kwargs in calls:
        assert "zstd" in args or kwargs.get("codec") == "zstd"


async def test_codec_is_normalised_before_it_leaves_python(streamer):
    """An env var carries whatever the operator typed."""
    s = _store(recursive_split=False, arrow_codec="  ZSTD  ")
    await s.store_data("k", ROWS)
    for args, kwargs in streamer.codec_args("jsonl_to_csv_and_nested_arrow"):
        assert "zstd" in args or kwargs.get("codec") == "zstd"


async def test_an_old_wheel_that_rejects_the_codec_still_writes_the_csv(monkeypatch):
    """The window the rollout order exists to avoid, made survivable anyway.

    If somebody flips the env before the wheel lands, the sidecar is lost — but
    the CSV, which is the authoritative artifact, must still be written and the
    ref must still be valid.
    """
    class _OldWheel(_RecordingStreamer):
        def jsonl_to_csv_and_nested_arrow(self, jsonl_path, csv_path, arrow_path):
            return self._write(jsonl_path, csv_path, arrow_path)

    monkeypatch.setattr(dss, "_STREAMER", _OldWheel())
    s = _store(recursive_split=False, arrow_codec="zstd")
    ref = await s.store_data("k", ROWS)
    assert ref.get("file_id"), "the CSV ref was lost when the codec was rejected"
