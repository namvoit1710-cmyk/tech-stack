"""SA-1905 C3/E — DataStoreService streaming CSV path (native producer + gate).

A large list-of-dicts is converted to CSV by the native streaming producer
instead of the in-RAM ``dict_to_csv`` (5.5-9.3× blow-up). The streaming path
must produce a file_ref that materializes to the SAME data as the legacy path,
and the feature flag / size threshold must gate it correctly.

Skipped when the native ``json_csv_streamer`` wheel isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("json_csv_streamer")

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _rows(n):
    return [{"id": i, "sku": f"S{i}", "qty": i % 5, "meta": {"w": i % 3}} for i in range(n)]


def _store(enabled, threshold):
    return DataStoreService(
        MockFileServiceClient(), CsvConverter(),
        chunked_output_enabled=enabled, chunking_threshold_bytes=threshold,
    )


async def test_streaming_roundtrips_identically_to_legacy():
    rows = _rows(50)
    streaming = _store(enabled=True, threshold=1)      # 1 byte → always streams
    legacy = _store(enabled=False, threshold=10**9)

    ref_s = await streaming.store_data("k", rows)
    ref_l = await legacy.store_data("k", rows)
    assert is_file_ref(ref_s) and is_file_ref(ref_l)

    out_s = await streaming.materialize(ref_s)
    out_l = await legacy.materialize(ref_l)
    assert out_s == rows, "streaming path must round-trip the data losslessly"
    assert out_s == out_l, "streaming and legacy paths must be semantically identical"


async def test_materialize_range_equivalent_to_materialize_slice():
    # SA-1905 D2.4: a lazy row-range read must reconstruct IDENTICALLY to a
    # full materialize — column_types cast (qty/id -> int) + nested dict
    # un-flatten (meta.w -> meta:{w}) — so a lazily-iterated loop item is the
    # same value the eager path produced.
    rows = _rows(30)
    store = _store(enabled=True, threshold=1)   # streams -> flat CSV, artifacts []
    ref = await store.store_data("k", rows)
    assert store.is_lazy_range_eligible(ref) is True

    full = await store.materialize(ref)
    page = await store.materialize_range(ref, 10, 5)
    assert page == full[10:15], "lazy slice must equal the eager slice byte-for-byte"
    assert page[0]["meta"] == {"w": 10 % 3}, "nested dict reconstructed"
    assert isinstance(page[0]["qty"], int), "column_types cast, not a raw string"
    assert await store.materialize_range(ref, 999, 5) == [], "out-of-range -> empty"


def test_is_lazy_range_eligible_gating():
    store = _store(enabled=True, threshold=1)
    flat = {"__file_ref": True, "file_id": "f", "artifacts": []}
    assert store.is_lazy_range_eligible(flat) is True
    assert store.is_lazy_range_eligible({**flat, "file_id": ""}) is False   # no file_id
    assert store.is_lazy_range_eligible({**flat, "artifacts": [{"x": 1}]}) is False  # nested
    assert store.is_lazy_range_eligible({"a": 1}) is False                  # not a ref
    assert store.is_lazy_range_eligible([1, 2]) is False


async def test_gate_disabled_never_streams():
    store = _store(enabled=False, threshold=1)
    assert store._should_stream(_rows(1000)) is False


async def test_gate_below_threshold_uses_legacy():
    store = _store(enabled=True, threshold=10**9)  # huge threshold
    assert store._should_stream(_rows(3)) is False


async def test_gate_true_for_any_large_list():
    store = _store(enabled=True, threshold=1)
    assert store._should_stream(_rows(100)) is True
    assert store._should_stream([]) is False          # empty
    assert store._should_stream({"a": 1}) is False     # dict, not a list
    # SA-1977 — a list of SCALARS now streams too. It used to be excluded
    # because ``_flatten`` could not write it at all; now that it can (the
    # reserved ``__value`` column), a 500 MB list of strings blows up
    # ``values_to_csv`` in RAM exactly like a list of objects blew up
    # ``dict_to_csv``, so it needs the streamer for the same reason.
    assert store._should_stream(["a", "b"]) is True


async def test_streaming_column_types_drive_lossless_cast():
    # qty/id are numbers, meta.w is a number — column_types from the streamer
    # must make materialize cast them back to ints, not strings.
    rows = _rows(20)
    store = _store(enabled=True, threshold=1)
    ref = await store.store_data("k", rows)
    assert ref["column_types"].get("qty") == "number"
    out = await store.materialize(ref)
    assert all(isinstance(r["qty"], int) for r in out)
