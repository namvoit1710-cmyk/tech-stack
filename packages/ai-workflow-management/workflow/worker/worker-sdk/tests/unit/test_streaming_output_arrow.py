"""SA-1905 — the worker's Arrow sidecar, tested at the converter.

Deliberately NOT through the FastAPI route: the thing worth pinning is that a
sidecar failure cannot change the CSV outcome, and the route adds nothing to
that question while adding a web framework to the test's dependency set.

The invariant under test, stated once: **the CSV ref must be identical whether
the sidecar succeeds, is disabled, or blows up.** The outer ``except`` in
``convert`` returns ``None``, which means *keep the output inline* — and an
oversized inline output is SA-1951: it rides a NodeOutputProduced event into an
Event Mesh publish, 413s at ~1 MB, and the run hangs forever with no error. A
sidecar hiccup must never be able to buy that.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.unit._native_streamer_worker import load as _load_native  # noqa: F401

jcs = _load_native()

from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
    ARROW_CONTENT_TYPE,
    StreamingOutputConverter,
)

ROWS = [{"id": i, "sku": f"S{i:04d}", "tags": ["a", "b"]} for i in range(400)]


class FakeUploader:
    """Records every upload and hands back a deterministic id."""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[tuple[str, str, int]] = []
        self._fail_on = fail_on

    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        if self._fail_on and self._fail_on in filename:
            raise RuntimeError("upload refused")
        self.calls.append((filename, content_type, len(file_bytes)))
        return f"fid-{filename}"


def _convert(uploader, *, arrow: bool, threshold: int = 1):
    conv = StreamingOutputConverter(
        uploader, enabled=True, threshold_bytes=threshold, arrow_sidecar=arrow,
    )
    return asyncio.run(conv.convert("t-1", "rows", ROWS))


def test_without_the_sidecar_nothing_changes():
    up = FakeUploader()
    ref = _convert(up, arrow=False)
    assert ref is not None
    assert ref["file_id"] == "fid-t-1-rows.csv"
    # ABSENT, not empty: absence is the encoding of "no sidecar", so a ref
    # without one keeps exactly the key set it had before this feature.
    assert "arrow_file_id" not in ref
    assert [c[0] for c in up.calls] == ["t-1-rows.csv"]


def test_with_the_sidecar_the_csv_ref_is_unchanged_and_a_second_file_appears():
    up = FakeUploader()
    ref = _convert(up, arrow=True)
    assert ref is not None
    # the CSV half is exactly what it was
    assert ref["file_id"] == "fid-t-1-rows.csv"
    assert ref["row_count"] == len(ROWS)
    assert ref["value_kind"] == "array"
    # and the sidecar rode alongside it
    assert ref["arrow_file_id"] == "fid-t-1-rows.arrow"
    assert ref["arrow_size_bytes"] > 0

    names = {c[0]: c[1] for c in up.calls}
    assert names["t-1-rows.csv"] == "text/csv"
    # Load-bearing: the file service canonicalises anything named *.csv or with
    # a csv mime through polars, which would rewrite — and destroy — Arrow IPC.
    assert names["t-1-rows.arrow"] == ARROW_CONTENT_TYPE
    assert not names["t-1-rows.arrow"].endswith("csv")


def test_the_csv_bytes_do_not_move_when_the_sidecar_is_added():
    off, on = FakeUploader(), FakeUploader()
    ref_off, ref_on = _convert(off, arrow=False), _convert(on, arrow=True)
    csv_off = next(c for c in off.calls if c[0].endswith(".csv"))
    csv_on = next(c for c in on.calls if c[0].endswith(".csv"))
    assert csv_off[2] == csv_on[2], "the steward's CSV changed size"
    # the ref metadata the control plane records is identical too
    for key in ("file_id", "row_count", "columns", "column_types", "item_kind"):
        assert ref_off[key] == ref_on[key], key


def test_a_failing_sidecar_upload_still_returns_the_csv_ref():
    """The whole point. ``None`` here would mean 'keep it inline' — SA-1951."""
    up = FakeUploader(fail_on=".arrow")
    ref = _convert(up, arrow=True)
    assert ref is not None, "a sidecar failure must not force the output inline"
    assert ref["file_id"] == "fid-t-1-rows.csv"
    assert "arrow_file_id" not in ref


def test_below_threshold_nothing_is_written_at_all():
    up = FakeUploader()
    conv = StreamingOutputConverter(
        up, enabled=True, threshold_bytes=10 ** 9, arrow_sidecar=True,
    )
    assert asyncio.run(conv.convert("t-1", "rows", ROWS)) is None
    assert up.calls == []
