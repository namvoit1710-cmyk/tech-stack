"""SA-1910 — a DICT output streams as the nested artifact tree, not inline.

This is the path that removes a live failure. `should_stream` required a list,
so a code node returning `{"title": …, "rows": [30 000 rows]}` — the exact shape
that produces a nested tree — could not stream. Its 2.3 MB went onto
`workflow.results` inline and the broker refused it:

    message for 'simplemdg/event/core-2/workflow.results' is 2314343 bytes,
    over the 1048576-byte broker cap

The run failed with the output already computed. Building the tree worker-side
turns that message back into a pointer.

The tree itself comes from the crate, which the control plane uses too, so these
tests do not re-check the split — `test_split_rust_vs_python.py` does that. What
is checked here is the part that is only in the worker: that the ref it spells
is one the control plane can read.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from ._native_streamer_worker import load

_STREAMER = load()

pytestmark = pytest.mark.skipif(
    not hasattr(_STREAMER, "jsonl_split_recursive"),
    reason="native wheel predates jsonl_split_recursive — rebuild it",
)

from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (  # noqa: E402
    FILE_REF_MARKER,
    StreamingOutputConverter,
)


class _FakeUploader:
    """Hands back a distinct file_id per upload, so a tree that reuses one is
    caught rather than looking plausible."""

    def __init__(self):
        self.uploads = []

    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        self.uploads.append({
            "filename": filename, "bytes": file_bytes,
            "content_type": content_type, "idempotency_key": idempotency_key,
        })
        return f"file-{len(self.uploads)}"


class _FailingUploader(_FakeUploader):
    def __init__(self, fail_on: int):
        super().__init__()
        self._fail_on = fail_on

    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        fid = await super().upload(file_bytes, filename, content_type, idempotency_key)
        return "" if len(self.uploads) == self._fail_on else fid


def _payload(n=40):
    return {
        "title": "shadow probe",
        "count": n,
        "rows": [{"id": i, "zip": "03230", "meta": {"w": i % 3}} for i in range(n)],
    }


def _conv(uploader, **kw):
    kw.setdefault("enabled", True)
    kw.setdefault("threshold_bytes", 1)
    return StreamingOutputConverter(uploader, **kw)


def _csv_rows(raw: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def test_a_dict_now_streams():
    """The regression under test: this returned False, and the run died."""
    assert _conv(_FakeUploader()).should_stream(_payload()) is True


def test_a_small_dict_still_rides_inline():
    conv = _conv(_FakeUploader(), threshold_bytes=10 * 1024 * 1024)
    assert conv.should_stream(_payload(2)) is False


@pytest.mark.parametrize("value", [{}, [], None, 0, "", "text", 7])
def test_nothing_else_changed_about_the_gate(value):
    assert _conv(_FakeUploader()).should_stream(value) is False


def test_the_gate_is_still_off_when_the_feature_is(value=None):
    conv = _conv(_FakeUploader(), enabled=False)
    assert conv.should_stream(_payload()) is False


def test_the_dict_estimate_does_not_serialize_every_row():
    """`_estimate_bytes` samples row 0 × count. A payload whose rows are all
    identical must therefore estimate to roughly its real size — if the sampling
    were dropped for a full serialize this test would still pass, so it is the
    ratio that is asserted, not the mechanism."""
    conv = _conv(_FakeUploader())
    payload = _payload(1000)
    real = len(json.dumps(payload).encode())
    est = conv._estimate_bytes(payload)
    assert 0.5 * real <= est <= 2.0 * real, (est, real)


# ---------------------------------------------------------------------------
# the ref the control plane has to read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dict_becomes_a_tree_ref_not_a_flat_csv():
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", _payload())

    assert ref is not None and ref[FILE_REF_MARKER] is True
    # An OBJECT root. The CP cannot infer this from the CSV — a one-row table
    # and a single object are indistinguishable once written (SA-1977).
    assert ref["value_kind"] == "object"
    assert ref["item_kind"] == ""

    arts = ref["artifacts"]
    assert [a["name"] for a in arts] == ["rows"]
    art = arts[0]
    assert art["artifact_type"] == "nested_array"
    assert art["parent_row_key_column"] == "__parent_row_id"
    assert art["value_kind"] == "array" and art["item_kind"] == "object"
    assert art["artifact_id"] == "rows"          # add_artifact's default
    assert art["row_count"] == 40
    assert art["artifacts"] == []                # recursive by construction


@pytest.mark.asyncio
async def test_the_root_and_the_child_are_different_files():
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", _payload())
    assert ref["file_id"] != ref["artifacts"][0]["file_id"]
    assert len(up.uploads) == 2


@pytest.mark.asyncio
async def test_declared_columns_include_row_id_and_match_the_csv():
    """The data factory identifies rows BY `__row_id`, so it has to be declared.
    And `columns` must describe the file it names, or the FE's descriptor tree
    is a schema for something else."""
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", _payload(5))

    by_name = {u["filename"]: u["bytes"] for u in up.uploads}
    root_hdr = _csv_rows(by_name["t1-result.csv"])[0].keys()
    child_hdr = _csv_rows(by_name["t1-result-rows.csv"])[0].keys()

    assert list(ref["columns"]) == list(root_hdr)
    assert list(ref["artifacts"][0]["columns"]) == list(child_hdr)
    assert "__row_id" in ref["columns"]
    assert "__row_id" in ref["artifacts"][0]["columns"]
    assert "__parent_row_id" in ref["artifacts"][0]["columns"]


@pytest.mark.asyncio
async def test_child_rows_join_back_to_the_parent_row():
    up = _FakeUploader()
    await _conv(up).convert("t1", "result", _payload(3))
    child = _csv_rows({u["filename"]: u["bytes"] for u in up.uploads}["t1-result-rows.csv"])
    # one parent row (a dict root), so every child points at __row_id 0
    assert [r["__parent_row_id"] for r in child] == ["0", "0", "0"]
    assert [r["__row_id"] for r in child] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_the_idempotency_key_is_the_hash_of_the_bytes_uploaded():
    """From the manifest, not recomputed. A key over anything other than the
    exact bytes makes the file service dedupe against content it does not hold."""
    import hashlib

    up = _FakeUploader()
    await _conv(up).convert("t1", "result", _payload(4))
    for u in up.uploads:
        assert u["idempotency_key"] == f"sha256:{hashlib.sha256(u['bytes']).hexdigest()}"


@pytest.mark.asyncio
async def test_nested_tables_recurse_into_child_artifacts():
    payload = {"orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]},
                          {"id": 2, "items": [{"n": 3}]}]}
    ref = await _conv(_FakeUploader()).convert("t1", "result", payload)
    orders = ref["artifacts"][0]
    assert orders["name"] == "orders" and orders["row_count"] == 2
    items = orders["artifacts"][0]
    assert items["name"] == "items" and items["row_count"] == 3
    assert items["parent_row_key_column"] == "__parent_row_id"


# ---------------------------------------------------------------------------
# failure is inline, never half a tree
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_child_upload_keeps_the_output_inline():
    """A half-uploaded tree is a ref whose join silently loses rows. Returning
    None costs an inline payload; returning a partial tree costs the data."""
    ref = await _conv(_FailingUploader(fail_on=2)).convert("t1", "result", _payload())
    assert ref is None


@pytest.mark.asyncio
async def test_a_failed_root_upload_keeps_the_output_inline():
    assert await _conv(_FailingUploader(fail_on=1)).convert("t1", "result", _payload()) is None


@pytest.mark.asyncio
async def test_an_uploader_that_raises_does_not_raise_out():
    class _Boom(_FakeUploader):
        async def upload(self, *a, **k):
            raise RuntimeError("file service down")

    # The async execute path calls this from a background task that is not
    # wrapped everywhere; a raise here would kill it with no callback and hang
    # the node.
    assert await _conv(_Boom()).convert("t1", "result", _payload()) is None


@pytest.mark.asyncio
async def test_the_list_path_is_untouched():
    """The dict branch is additive. A list must still take the flat writer —
    `artifacts: []`, `value_kind: array` — because that is what is live."""
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", [{"a": i} for i in range(20)])
    assert ref["value_kind"] == "array"
    assert ref["artifacts"] == []
    assert len(up.uploads) == 1


# ---------------------------------------------------------------------------
# the sidecar rides along, and cannot cost the tree
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_arrow_sidecar_is_off_unless_asked():
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", _payload())
    assert "arrow_file_id" not in ref
    assert all(u["content_type"] == "text/csv" for u in up.uploads)


@pytest.mark.asyncio
async def test_the_arrow_sidecar_is_a_separate_non_csv_file():
    up = _FakeUploader()
    ref = await _conv(up, arrow_sidecar=True).convert("t1", "result", _payload())
    arrow = [u for u in up.uploads if u["filename"].endswith(".arrow")]
    assert len(arrow) == 1
    # NOT *.csv, or the file service's polars canonicalisation rewrites — and
    # destroys — the Arrow IPC file.
    assert arrow[0]["content_type"] == "application/vnd.apache.arrow.file"
    assert ref["arrow_file_id"] and ref["arrow_size_bytes"] == len(arrow[0]["bytes"])


@pytest.mark.asyncio
async def test_a_broken_sidecar_still_yields_the_tree():
    """Trading a working pointer for an inline value the broker refuses is the
    one outcome a best-effort optimisation must never buy."""
    class _NoArrow(_FakeUploader):
        async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
            if filename.endswith(".arrow"):
                raise RuntimeError("sidecar upload exploded")
            return await super().upload(file_bytes, filename, content_type, idempotency_key)

    ref = await _conv(_NoArrow(), arrow_sidecar=True).convert("t1", "result", _payload())
    assert ref is not None
    assert "arrow_file_id" not in ref
    assert ref["artifacts"][0]["name"] == "rows"


# ---------------------------------------------------------------------------
# SA-1910 — report every file_id created, so the CP can delete the loose ones
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_uploaded_file_id_is_reported():
    """The worker cannot tell which of these survive — `keep_inline`,
    `output_binding` and the rest of `complete_task` decide that, and it sees
    none of it. So it reports what it made and lets the CP delete the rest."""
    up = _FakeUploader()
    uploaded: list[str] = []
    ref = await _conv(up).convert("t1", "result", _payload(), uploaded)

    assert uploaded == [u_id for u_id in (ref["file_id"], ref["artifacts"][0]["file_id"])]
    assert len(uploaded) == len(up.uploads)


@pytest.mark.asyncio
async def test_the_sidecar_is_reported_too():
    up = _FakeUploader()
    uploaded: list[str] = []
    ref = await _conv(up, arrow_sidecar=True).convert("t1", "result", _payload(), uploaded)

    assert ref["arrow_file_id"] in uploaded
    assert len(uploaded) == 3          # root + child + sidecar


@pytest.mark.asyncio
async def test_a_deep_tree_reports_every_level():
    up = _FakeUploader()
    uploaded: list[str] = []
    payload = {"orders": [{"id": 1, "items": [{"n": 1}]}]}
    await _conv(up).convert("t1", "result", payload, uploaded)
    assert len(uploaded) == 3          # root + orders + items
    assert len(uploaded) == len(up.uploads)


@pytest.mark.asyncio
async def test_the_list_path_reports_its_upload_as_well():
    up = _FakeUploader()
    uploaded: list[str] = []
    ref = await _conv(up).convert("t1", "result", [{"a": i} for i in range(20)], uploaded)
    assert uploaded == [ref["file_id"]]


@pytest.mark.asyncio
async def test_files_uploaded_before_a_failure_are_still_reported():
    """The point of the list: the ones worth reporting are exactly the ones
    nothing ends up pointing at. A convert that returns None has left files
    behind, and losing their ids loses the only chance to delete them."""
    up = _FailingUploader(fail_on=2)
    uploaded: list[str] = []
    assert await _conv(up).convert("t1", "result", _payload(), uploaded) is None
    assert uploaded == ["file-1"], "the root upload succeeded and must be reported"


@pytest.mark.asyncio
async def test_omitting_the_list_changes_nothing():
    """Back-compat: the parameter is optional and every existing caller omits it."""
    up = _FakeUploader()
    ref = await _conv(up).convert("t1", "result", _payload())
    assert ref is not None and ref["artifacts"][0]["name"] == "rows"
