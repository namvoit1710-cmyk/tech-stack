"""SA-1905 F1 (SA-1917) — MERGE / nested file_ref passthrough.

A MERGE fan-in builds ``merged_data[key] = variables[source]`` where a
branch's output may itself be a file-backed reference. When that dict is
stored via ``store_data``, ``_flatten`` used to recurse INTO the nested
file_ref — shredding the pointer into ``branchA.file_id`` /
``branchA.artifacts`` / … columns (22-column explosion, lossy round-trip,
and the child's schema polluting the parent's column_types), so downstream
deep-path reads could not resolve through it.

Fix: ``_flatten`` keeps a nested file_ref whole in a single JSON cell, and
``FileBackedResolverStrategy`` recurses into it for deep paths.

AC1 — nested refs survive store→materialize as pointers (round-trip).
AC2 — ``{{$merge.branchA.items[0].sku}}`` resolves lazily THROUGH the
      merged ref (one child fetch, no full download of the parent's data).
"""

import pytest

from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


@pytest.fixture
def data_store():
    return DataStoreService(MockFileServiceClient(), CsvConverter())


@pytest.fixture
def strategy(data_store):
    return FileBackedResolverStrategy(data_store)


# ── AC1 — round-trip preservation ──

async def test_nested_file_ref_survives_store_materialize(data_store):
    child = await data_store.store_data("branchA", {"items": [{"sku": "A"}, {"sku": "B"}]})
    assert is_file_ref(child)

    merged = await data_store.store_data("merge1", {"branchA": child, "count": 2})
    restored = await data_store.materialize(merged)

    assert restored["count"] == 2
    assert is_file_ref(restored["branchA"]), "nested ref must round-trip as a pointer, not flattened columns"
    assert restored["branchA"]["file_id"] == child["file_id"]


async def test_two_branch_refs_both_preserved(data_store):
    a = await data_store.store_data("a", {"rows": [{"x": 1}]})
    b = await data_store.store_data("b", {"rows": [{"y": 2}]})
    merged = await data_store.store_data("merge1", {"a": a, "b": b})
    restored = await data_store.materialize(merged)
    assert is_file_ref(restored["a"]) and is_file_ref(restored["b"])
    assert restored["a"]["file_id"] == a["file_id"]
    assert restored["b"]["file_id"] == b["file_id"]


# ── AC2 — deep path resolves through the merged nested ref ──

async def test_deep_path_resolves_through_merged_nested_ref(strategy, data_store):
    child = await data_store.store_data(
        "branchA", {"items": [{"sku": "A"}, {"sku": "B"}, {"sku": "C"}]},
    )
    merged = await data_store.store_data("merge1", {"branchA": child})

    assert await strategy.try_resolve(merged, ("branchA", "items", 0, "sku")) == "A"
    assert await strategy.try_resolve(merged, ("branchA", "items", 2, "sku")) == "C"


async def test_whole_nested_ref_returns_pointer(strategy, data_store):
    child = await data_store.store_data("branchA", {"items": [{"sku": "A"}]})
    merged = await data_store.store_data("merge1", {"branchA": child})

    result = await strategy.try_resolve(merged, ("branchA",))
    assert is_file_ref(result), "whole nested value stays a pointer (for worker passing)"
