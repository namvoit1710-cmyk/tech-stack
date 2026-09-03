"""Phase 3 — a large STRING output can be relayed as a pointer.

Until now ``should_stream`` took ``list``/``dict`` only, so a string field rode
the event inline at any size. That is what failed run
edda4f82-4a09-4587-beb5-8ed615ab8219 on tenant-1: the oversized field was
``resolved_body``, a ``str``, and the advice the failure printed (worker-side
streaming, SA-1944) could not have touched it.

The storage shape is not new. The control plane already models scalars: they go
in the reserved ``__value`` column (``value_kind.uses_value_column`` is true for
every SCALAR_KIND) and ``CsvConverter.rows_to_value`` unwraps them on read. What
was missing is a WRITER on the worker side — and, as these tests pin, a READER:
``HttpFileRefResolver`` knew only ``object``/``array`` and would have called a
scalar ref an object, handing the next node ``{"__value": "..."}`` instead of the
string. Shipping the writer without the reader would be a corruption path, so
both are here.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

pytest.importorskip("json_csv_streamer")

from worker_sdk.layer2_application.services.result_budget import budget_verdict
from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
    StreamingOutputConverter,
)
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
)


class _Uploader:
    def __init__(self):
        self.uploads = []

    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        self.uploads.append((file_bytes, filename, content_type))
        return f"file-{len(self.uploads)}"


def _converter(scalars: bool, threshold: int = 1024):
    return StreamingOutputConverter(
        _Uploader(), enabled=True, threshold_bytes=threshold, stream_scalars=scalars,
    )


# A string that also exercises CSV quoting: commas, quotes and a newline.
_TRICKY = ('a,b"c' + "\n" + "d") * 500


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def test_a_large_string_does_not_stream_while_the_flag_is_off():
    """Default off: the writer must never get ahead of the readers."""
    assert _converter(scalars=False).should_stream("x" * 5000) is False


def test_a_large_string_streams_when_the_flag_is_on():
    assert _converter(scalars=True).should_stream("x" * 5000) is True


def test_a_small_string_still_rides_inline():
    """Below threshold there is nothing to gain and a round-trip to lose."""
    assert _converter(scalars=True).should_stream("short") is False


def test_an_empty_string_is_not_streamed():
    assert _converter(scalars=True).should_stream("") is False


@pytest.mark.asyncio
async def test_convert_declares_the_scalar_kind_not_array():
    """``array`` would read back as a one-element LIST, not the string."""
    conv = _converter(scalars=True)
    ref = await conv.convert("t1", "resolved_body", _TRICKY)

    assert ref is not None
    assert ref["__file_ref"] is True
    assert ref["value_kind"] == "string"
    assert ref["item_kind"] == ""
    assert ref["row_count"] == 1
    assert ref["columns"] == ["__value"]
    assert ref["content_type"] == "text/csv"


@pytest.mark.asyncio
async def test_the_uploaded_csv_holds_the_exact_string():
    """Proof at the bytes, not at the ref: quoting must survive."""
    uploader = _Uploader()
    conv = StreamingOutputConverter(
        uploader, enabled=True, threshold_bytes=1024, stream_scalars=True,
    )
    await conv.convert("t1", "resolved_body", _TRICKY)

    csv_bytes = uploader.uploads[0][0]
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["__value"] == _TRICKY


# ---------------------------------------------------------------------------
# Reader — the half that would have corrupted data if it shipped late
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, rows):
        self._payload = {"data": {"data": rows}}
        self.content = json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def get(self, url, params=None):
        file_id = url.split("/odata/")[1].split("/data")[0]
        skip = int((params or {}).get("$skip", "0"))
        return _FakeResponse(self._store.get(file_id, []) if skip == 0 else [])


def _resolver(store):
    r = HttpFileRefResolver("http://fs:8000")
    r._client = _FakeClient(store)
    return r


def _scalar_ref(kind, column_type):
    return {
        "__file_ref": True,
        "file_id": "f1",
        "content_type": "text/csv",
        "row_count": 1,
        "columns": ["__value"],
        "column_types": {"__value": column_type},
        "value_kind": kind,
        "item_kind": "",
        "artifacts": [],
    }


def test_a_string_ref_resolves_to_the_string_not_a_one_key_dict():
    """The SA-1977 defect, on the worker side of the wire."""
    store = {"f1": [{"__row_id": "0", "__value": "hello, world"}]}
    out = _resolver(store).resolve_inputs({"body": _scalar_ref("string", "string")})

    assert out["body"] == "hello, world"


def test_a_number_ref_resolves_to_a_number():
    store = {"f1": [{"__row_id": "0", "__value": "42"}]}
    out = _resolver(store).resolve_inputs({"n": _scalar_ref("number", "number")})

    assert out["n"] == 42


def test_a_scalar_ref_with_no_rows_resolves_to_none():
    """``empty_for_kind`` says a scalar with nothing stored is None, not {}."""
    out = _resolver({"f1": []}).resolve_inputs({"body": _scalar_ref("string", "string")})

    assert out["body"] is None


def test_an_object_ref_is_untouched_by_the_scalar_branch():
    """Regression fence: the shapes that worked must keep working."""
    ref = {
        "__file_ref": True, "file_id": "f1", "content_type": "text/csv",
        "row_count": 1, "columns": ["a"], "column_types": {"a": "string"},
        "value_kind": "object", "item_kind": "", "artifacts": [],
    }
    store = {"f1": [{"__row_id": "0", "a": "x"}]}

    assert _resolver(store).resolve_inputs({"o": ref})["o"] == {"a": "x"}


# ---------------------------------------------------------------------------
# The attribution message has to keep telling the truth
# ---------------------------------------------------------------------------

def test_a_string_is_still_called_not_streamable_while_the_flag_is_off():
    msg = budget_verdict({"resolved_body": "x" * 5000}, 1024)
    assert "NOT-STREAMABLE" in msg


def test_a_string_is_no_longer_called_not_streamable_once_scalars_stream():
    msg = budget_verdict({"resolved_body": "x" * 5000}, 1024, scalars_streamable=True)
    assert msg is not None
    assert "resolved_body" in msg
    assert "NOT-STREAMABLE" not in msg
