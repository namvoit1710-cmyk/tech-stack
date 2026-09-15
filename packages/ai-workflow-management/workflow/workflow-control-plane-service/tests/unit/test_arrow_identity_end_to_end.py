"""SA-1905 — the Arrow read path against the EXISTING identity contract.

The criterion, set before any of this was written: *if the Arrow path cannot
pass the suite that already exists, unchanged, it is not ready.* So this file
takes the shapes ``test_recursive_nested_split`` and ``test_nested_array_roundtrip``
already assert — the ones that produce a tree of CSV files joined on
``__parent_row_id`` — and runs them end to end through ``DataStoreService`` with
the sidecar written and read.

It is a separate file rather than a parametrisation of those two, deliberately:
they are the CSV baseline. If a future change breaks the CSV path, that must
show up as those suites going red on their own terms, not as a mysterious
half-failure of a matrix.

``chunking_threshold_bytes=1`` because these payloads are a few hundred bytes
and the sidecar is only worth writing above the streaming threshold in
production. The threshold is an economics decision, not a correctness one; the
shapes below are chosen for what they exercise, not for their size.
"""

from __future__ import annotations

import pytest

from tests.unit._native_streamer import load as _load_native

_load_native()  # skip on a laptop with no wheel; FAIL in CI (see the helper)

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

# The recursive-split shapes, verbatim from `test_recursive_nested_split.py`.
SHAPES = [
    {"title": "t", "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]},
    {"order": {"id": 1, "lines": [{"sku": "A"}, {"sku": "B"}]}},
    {"orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]}, {"id": 2, "items": [{"n": 3}]}]},
    {"a": [{"id": 1, "b": [{"id": 10, "c": [{"v": 100}, {"v": 101}]}]}]},
    {"name": "x", "count": 5, "meta": {"w": "W1"}, "tags": ["a", "b"], "rows": [{"r": 1}, {"r": 2}]},
    {"items": [{"n": 1}, {"n": 2}]},
]

LIST_SHAPES = [
    [{"id": 1, "items": [{"n": 1}]}, {"id": 2, "items": [{"n": 2}, {"n": 3}]}],
    [{"a": 1}, {"a": 2}],
]


def _store(*, arrow: bool, recursive: bool = True) -> DataStoreService:
    return DataStoreService(
        MockFileServiceClient(),
        CsvConverter(),
        recursive_split=recursive,
        chunking_threshold_bytes=1,
        arrow_read_mode="on" if arrow else "off",
        arrow_write_enabled=arrow,
        arrow_strict_parity=True,
    )


@pytest.mark.parametrize("data", SHAPES + LIST_SHAPES)
async def test_identity_holds_with_the_sidecar_on(data):
    s = _store(arrow=True)
    ref = await s.store_data("k", data)
    assert ref.get("arrow_file_id"), "no sidecar was written — the test proves nothing"
    assert await s.materialize(ref) == data


@pytest.mark.parametrize("data", SHAPES + LIST_SHAPES)
async def test_both_paths_return_the_same_value(data):
    """Same input, same service, one flag apart."""
    off, on = _store(arrow=False), _store(arrow=True)
    ref_off = await off.store_data("k", data)
    ref_on = await on.store_data("k", data)
    assert "arrow_file_id" not in ref_off
    assert await off.materialize(ref_off) == await on.materialize(ref_on)


@pytest.mark.parametrize("data", SHAPES)
async def test_the_csv_tree_is_unchanged_by_writing_a_sidecar(data):
    """The steward's files must not move.

    Compares the BYTES, not the ``file_id``s: ``MockFileServiceClient`` mints a
    ``uuid4`` per upload, so two runs of identical content get different ids.
    (The real client is content-addressed via ``X-Idempotency-Key``, but a test
    that leans on that would be asserting the client, not the writer.)
    """
    off, on = _store(arrow=False), _store(arrow=True)
    ref_off = await off.store_data("k", data)
    ref_on = await on.store_data("k", data)

    def shape_of(ref):
        """Everything the control plane records, minus the two sidecar keys and
        the ids — the declaration a reader actually depends on."""
        if not isinstance(ref, dict):
            return ref
        drop = {"arrow_file_id", "arrow_size_bytes", "file_id", "created_at"}
        out = {k: v for k, v in ref.items() if k not in drop}
        out["artifacts"] = [shape_of(a) for a in out.get("artifacts", [])]
        return out

    assert shape_of(ref_off) == shape_of(ref_on), "the declared metadata moved"

    async def tree_bytes(svc, ref):
        """Every CSV in the tree, in walk order."""
        out = [await svc._file_client.download_file(ref["file_id"])] if ref.get("file_id") else []
        for art in ref.get("artifacts", []):
            out.extend(await tree_bytes(svc, art))
        return out

    assert await tree_bytes(off, ref_off) == await tree_bytes(on, ref_on), (
        "a CSV in the tree changed bytes when the sidecar was added"
    )


async def test_a_missing_sidecar_file_falls_back_to_csv():
    """The pointer is a cache and its loss is legal — a ref that names a file
    the store does not have must read correctly, not raise."""
    data = SHAPES[0]
    s = _store(arrow=True)
    ref = await s.store_data("k", data)
    assert ref["arrow_file_id"]
    ref["arrow_file_id"] = "does-not-exist"
    assert await s.materialize(ref) == data


async def test_a_ref_rehydrated_without_the_pointer_still_reads():
    """``node_output_rows.flatten`` projects a fixed field set and drops the
    pointer. That is by design; this is what it must mean."""
    data = SHAPES[3]
    s = _store(arrow=True)
    ref = await s.store_data("k", data)
    ref.pop("arrow_file_id", None)
    ref.pop("arrow_size_bytes", None)
    assert await s.materialize(ref) == data


async def test_shadow_mode_returns_the_csv_value():
    data = SHAPES[2]
    writer = _store(arrow=True)
    ref = await writer.store_data("k", data)

    shadow = DataStoreService(
        writer._file_client, CsvConverter(),
        recursive_split=True, chunking_threshold_bytes=1,
        arrow_read_mode="shadow", arrow_write_enabled=True, arrow_strict_parity=True,
    )
    assert await shadow.materialize(ref) == data
