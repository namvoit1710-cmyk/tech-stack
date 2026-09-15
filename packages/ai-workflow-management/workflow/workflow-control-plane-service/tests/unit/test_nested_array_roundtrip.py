"""SA-1905 D1 — recursive round-trip invariant: materialize(store(x)) == x.

Confirms that arbitrarily nested structures survive store_data → materialize:
  * top-level array-of-dicts  -> split into an array_field artifact, re-attached
  * nested array-of-dicts     -> kept as a JSON cell, reconstructed by _unflatten
  * nested scalars / objects  -> dot-columns

The correctness AC (no data loss at any depth) holds today. Splitting a *nested*
array-of-dicts into its own queryable child artifact (OData per-row, avoiding
the 128 MB cell ceiling for very large nested arrays) is a scale enhancement
that belongs with the streaming recursive-spill (B1) — tracked separately; it
does NOT affect this round-trip invariant.
"""

from __future__ import annotations

import pytest

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _store():
    return DataStoreService(MockFileServiceClient(), CsvConverter())


@pytest.mark.parametrize("data", [
    # nested object + nested array-of-dicts + top-level array-of-dicts
    {
        "order_id": "O1",
        "customer": {"name": "Acme", "contacts": [{"type": "email", "v": "a@x.com"},
                                                  {"type": "phone", "v": "5"}]},
        "lines": [{"sku": "A", "qty": 2, "tax": {"rate": 0.1}},
                  {"sku": "B", "qty": 5, "tax": {"rate": 0.2}}],
    },
    # arrays of scalars + empty containers
    {"tags": ["x", "y"], "empty_list": [], "flag": True, "note": None, "n": 0},
    # doubly-nested arrays
    {"a": {"b": {"c": [{"d": [1, 2, 3]}, {"d": []}]}}},
])
async def test_materialize_store_roundtrip_is_identity(data):
    store = _store()
    ref = await store.store_data("nd", data)
    assert await store.materialize(ref) == data


async def test_top_level_array_of_dicts_is_split_to_artifact():
    store = _store()
    data = {"title": "t", "lines": [{"sku": "A"}, {"sku": "B"}]}
    ref = await store.store_data("nd", data)
    assert [a.get("name") for a in ref.get("artifacts", [])] == ["lines"]
    assert await store.materialize(ref) == data
