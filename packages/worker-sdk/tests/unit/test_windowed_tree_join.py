"""The windowed tree join — read a file-backed collection W root rows at a time.

Why this exists: the HTTP node's body is a file_ref, and today the worker turns the
whole collection into Python objects and then back into bytes to POST it. That middle
step is what forces every limit — RESOLVE_MAX_INPUT_BYTES, the pod size, and (through
GC thrash) the timeouts. Measured: 23.1 MB of wire cost ~110 MB of worker RSS, 4.8x.
Three ceiling raises in one day (1k -> 10k -> 69 MB payloads) each bought one step.

Streaming makes peak RAM a function of the WINDOW, not the payload. The risky part is
not the streaming — it is this join under windowing, so it is tested first and on its
own.

**The contract these tests enforce is parity.** The windowed reader must produce exactly
what ``_reconstruct_ref_rows`` / ``resolve_inputs`` produce today, for every shape and
every window size. Two expressions of one rule, disagreeing, is the defect the whole
file_ref layer keeps re-learning; the parity tests are what stop a second rule existing.

Reuses the mirror-writer from ``test_file_ref_rejoin`` so the trees under test are built
the way the control plane builds them, not the way this reader would like them.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.test_file_ref_rejoin import _build  # mirror-writer for CP trees
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
)


# ---------------------------------------------------------------------------
# Fake OData file service that honours $top/$skip AND $filter on __parent_row_id
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, rows):
        self._payload = {"data": {"data": rows}}
        self.content = json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FilteringClient:
    """Server-side $filter, verified live against the tenant-1 file service:
    `$filter=__parent_row_id ge 5000` really does start at 5000."""

    def __init__(self, store):
        self._store = store
        self.requests = []

    def get(self, url, params=None):
        file_id = url.split("/odata/")[1].split("/data")[0]
        params = params or {}
        self.requests.append((file_id, dict(params)))
        rows = list(self._store.get(file_id, []))

        flt = params.get("$filter")
        if flt:
            lo = hi = None
            for token in flt.split(" and "):
                bits = token.split()
                if len(bits) == 3 and bits[0] == "__parent_row_id":
                    if bits[1] == "ge":
                        lo = int(bits[2])
                    elif bits[1] == "lt":
                        hi = int(bits[2])
            def keep(r):
                v = r.get("__parent_row_id")
                if v in (None, ""):
                    return False
                v = int(v)
                return (lo is None or v >= lo) and (hi is None or v < hi)
            rows = [r for r in rows if keep(r)]

        skip = int(params.get("$skip", 0) or 0)
        top = params.get("$top")
        rows = rows[skip:]
        if top is not None:
            rows = rows[: int(top)]
        return _FakeResponse(rows)


def _resolver(store):
    r = HttpFileRefResolver("http://fs:8000")
    r._client = _FilteringClient(store)
    return r


def _windowed(store, ref, window):
    r = _resolver(store)
    return list(r.iter_objects(ref, window_size=window))


def _whole(store, ref):
    out = _resolver(store).resolve_inputs(ref)
    return out if isinstance(out, list) else [out]


# ---------------------------------------------------------------------------
# Parity — the contract
# ---------------------------------------------------------------------------

_SHAPES = {
    "flat list": [{"a": i, "b": f"s{i}"} for i in range(7)],
    "one child each": [
        {"id": i, "kids": [{"k": i * 10}]} for i in range(5)
    ],
    "fan-out n:1": [
        {"id": i, "kids": [{"k": j} for j in range(i)]} for i in range(6)
    ],
    "two levels": [
        {"id": i, "kids": [{"k": j, "grand": [{"g": j * 2}]} for j in range(2)]}
        for i in range(4)
    ],
    "two sibling children": [
        {"id": i, "left": [{"l": i}], "right": [{"r": i}, {"r": i + 1}]}
        for i in range(5)
    ],
    "scalars and nulls": [
        {"id": i, "n": None, "f": 1.5, "flag": i % 2 == 0, "kids": [{"k": i}]}
        for i in range(4)
    ],
}


@pytest.mark.parametrize("window", [1, 2, 3, 5, 1000])
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_windowed_output_equals_the_whole_read(shape, window):
    """The only contract that matters: same bytes out, whatever the window."""
    store = {}
    ref = _build(store, _SHAPES[shape])

    assert _windowed(store, ref, window) == _whole(store, ref), (
        f"{shape} diverged at window={window}"
    )


# ---------------------------------------------------------------------------
# The edges the parity cases can hide
# ---------------------------------------------------------------------------

def test_a_parent_with_no_children_has_no_field_not_an_empty_list():
    """`_reconstruct_ref_rows` only sets the field when the group exists.
    Emitting [] instead would be a different document."""
    store = {}
    ref = _build(store, [{"id": 0, "kids": [{"k": 1}]}, {"id": 1}])

    out = _windowed(store, ref, window=1)
    assert "kids" in out[0]
    assert "kids" not in out[1], "an absent group must stay absent, not become []"
    assert out == _whole(store, ref)


def test_children_are_grouped_by_parent_even_when_the_window_splits_them():
    """The join key is __parent_row_id, never arrival order or position."""
    store = {}
    data = [{"id": i, "kids": [{"k": f"{i}-{j}"} for j in range(3)]} for i in range(4)]
    ref = _build(store, data)

    for w in (1, 2, 3):
        out = _windowed(store, ref, w)
        for i, row in enumerate(out):
            assert [k["k"] for k in row["kids"]] == [f"{i}-{j}" for j in range(3)]


def test_children_arriving_out_of_order_still_group_correctly():
    store = {}
    ref = _build(store, [{"id": i, "kids": [{"k": i}]} for i in range(4)])
    child_file = [f for f in store if f != ref["file_id"]][0]
    store[child_file] = list(reversed(store[child_file]))

    assert _windowed(store, ref, 2) == _whole(store, ref)


def test_an_empty_collection_yields_nothing():
    store = {}
    ref = _build(store, [])
    assert _windowed(store, ref, 5) == _whole(store, ref)


def test_an_object_root_is_yielded_once_whatever_the_window():
    store = {}
    ref = _build(store, {"id": 1, "kids": [{"k": 1}, {"k": 2}]})

    out = _windowed(store, ref, window=1)
    assert len(out) == 1
    assert out == _whole(store, ref)


def test_a_window_larger_than_the_collection_is_one_window():
    store = {}
    ref = _build(store, [{"id": i} for i in range(3)])
    r = _resolver(store)
    windows = list(r.iter_root_windows(ref, window_size=100))

    assert len(windows) == 1
    assert len(windows[0]) == 3


def test_the_last_short_window_terminates_the_read():
    """A short page means end-of-data; not noticing it is an infinite loop."""
    store = {}
    ref = _build(store, [{"id": i} for i in range(5)])
    r = _resolver(store)
    windows = list(r.iter_root_windows(ref, window_size=2))

    assert [len(w) for w in windows] == [2, 2, 1]


# ---------------------------------------------------------------------------
# The point of the exercise: bounded reads
# ---------------------------------------------------------------------------

def test_it_reads_the_root_in_windows_rather_than_whole():
    store = {}
    ref = _build(store, [{"id": i, "kids": [{"k": i}]} for i in range(10)])
    r = _resolver(store)
    list(r.iter_objects(ref, window_size=2))

    root_reads = [p for f, p in r._client.requests if f == ref["file_id"]]
    tops = {int(p["$top"]) for p in root_reads if p.get("$top")}
    assert tops == {2}, f"root should be read 2 rows at a time, saw {tops}"
    assert len(root_reads) >= 5


def test_children_are_fetched_filtered_to_the_window_not_wholesale():
    """Without the $filter the child read is the whole child table every window —
    which would be worse than not windowing at all."""
    store = {}
    ref = _build(store, [{"id": i, "kids": [{"k": i}]} for i in range(10)])
    r = _resolver(store)
    list(r.iter_objects(ref, window_size=5))

    child_id = ref["artifacts"][0]["file_id"]
    child_reads = [p for f, p in r._client.requests if f == child_id]
    assert child_reads, "the child was never read"
    assert all("$filter" in p for p in child_reads), (
        "every child read must be scoped to the window by __parent_row_id"
    )
