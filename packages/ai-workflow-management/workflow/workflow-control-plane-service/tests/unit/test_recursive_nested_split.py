"""SA-1910 — recursive nested-array split (gated by recursive_split).

store_data pulls nested array-of-dicts (at any depth) into their own child CSV
artifacts stamped __parent_row_id; materialize recursively re-joins them at
their dotted paths. The split is lossless: materialize(store(x)) == x. Gate off
keeps the legacy depth-1 behaviour (nested arrays = JSON cells).
"""

from __future__ import annotations

import pytest

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _store(recursive=True):
    return DataStoreService(MockFileServiceClient(), CsvConverter(), recursive_split=recursive)


async def _roundtrip(data, recursive=True):
    s = _store(recursive)
    ref = await s.store_data("k", data)
    return await s.materialize(ref), ref


@pytest.mark.parametrize("data", [
    # top-level array-of-dicts
    {"title": "t", "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]},
    # array-of-dicts nested inside an object (order.lines)
    {"order": {"id": 1, "lines": [{"sku": "A"}, {"sku": "B"}]}},
    # parent-child arrays (orders[i].items) — the __parent_row_id case
    {"orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]}, {"id": 2, "items": [{"n": 3}]}]},
    # three levels deep
    {"a": [{"id": 1, "b": [{"id": 10, "c": [{"v": 100}, {"v": 101}]}]}]},
    # scalars + nested object + scalar array + array-of-dicts together
    {"name": "x", "count": 5, "meta": {"w": "W1"}, "tags": ["a", "b"], "rows": [{"r": 1}, {"r": 2}]},
    # only-arrays root (no scalar main CSV)
    {"items": [{"n": 1}, {"n": 2}]},
])
async def test_recursive_roundtrip_is_identity(data):
    out, _ref = await _roundtrip(data)
    assert out == data


async def test_list_input_with_nested_arrays_roundtrips():
    data = [{"id": 1, "items": [{"n": 1}]}, {"id": 2, "items": [{"n": 2}, {"n": 3}]}]
    out, _ref = await _roundtrip(data)
    assert out == data


async def test_heterogeneous_rows_do_not_gain_empty_arrays():
    # order 2 has no items -> must NOT get an items:[] key after round-trip.
    data = {"orders": [{"id": 1, "items": [{"n": 1}]}, {"id": 2}]}
    out, _ref = await _roundtrip(data)
    assert out == data
    assert "items" not in out["orders"][1]


async def test_split_actually_creates_nested_artifacts():
    data = {"orders": [{"id": 1, "items": [{"n": 1}]}]}
    s = _store(True)
    ref = await s.store_data("k", data)
    arts = ref.get("artifacts", [])
    orders = [a for a in arts if a.get("name") == "orders"]
    assert orders and orders[0]["artifact_type"] == "nested_array"
    assert orders[0].get("parent_row_key_column") == "__parent_row_id"
    # items is a grandchild artifact under orders (recursive)
    assert any(a.get("name") == "items" for a in orders[0].get("artifacts", []))


async def test_gate_off_keeps_nested_arrays_as_json_cells():
    data = {"order": {"lines": [{"sku": "A"}, {"sku": "B"}]}}
    out, ref = await _roundtrip(data, recursive=False)
    assert out == data  # still lossless (D1 JSON-cell round-trip)
    assert not any(a.get("artifact_type") == "nested_array" for a in ref.get("artifacts", []))


async def test_types_round_trip_at_depth():
    data = {"orders": [{"id": 1, "total": 9.5, "paid": True, "items": [{"qty": 3, "name": "x"}]}]}
    out, _ref = await _roundtrip(data)
    assert out == data
    o = out["orders"][0]
    assert o["id"] == 1 and o["total"] == 9.5 and o["paid"] is True
    assert o["items"][0]["qty"] == 3 and o["items"][0]["name"] == "x"
