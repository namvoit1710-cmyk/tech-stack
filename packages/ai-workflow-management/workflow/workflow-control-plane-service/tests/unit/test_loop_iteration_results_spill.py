"""SA-1905 F2 (SA-1918) — LOOP iteration_results bounded / streamed.

The loop accumulated one {index, item, output} entry per iteration into
loop_state["iteration_results"] and embedded the whole list in its final
output — unbounded RAM (and unbounded loop_state serialization) for a
many-iteration loop. F2 bounds the in-RAM window: a full window is spilled to a
durable chunk file_ref (replay-safe: only chunk file_ids live in loop_state),
and the loop's final output becomes a manifest file_ref that materialize
concatenates back into the full list.

Gated on a store + WORKER_CHUNKED_OUTPUT_ENABLED; OFF/no-store → results stay
inline (small loops byte-identical). Covers: spill boundary, gate off, no
store, residual flush + manifest build, and the materialize concat round-trip.
"""

from __future__ import annotations

import pytest

from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _store(enabled=True):
    # chunking_threshold huge → chunk spills use the in-RAM _store_list path
    # (fine for these small windows); _chunked_enabled drives F2's gate.
    return DataStoreService(
        MockFileServiceClient(), CsvConverter(),
        chunked_output_enabled=enabled, chunking_threshold_bytes=10 ** 9,
    )


def _driver(store, window=3):
    return EventDrivenLoopDriver(_resolver(), data_store_service=store, iteration_results_window=window)


def _entries(n):
    return [{"index": i, "item": {"n": i}, "output": {"r": i * 10}} for i in range(n)]


async def _accumulate(driver, loop_state, entries):
    for e in entries:
        await driver._accumulate_result(loop_state, e)


async def test_spills_at_window_boundary_and_clears():
    driver = _driver(_store(enabled=True), window=3)
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    await _accumulate(driver, loop_state, _entries(7))
    # 7 entries, window 3 → spill at 3 and 6 (2 chunks); 1 residual in RAM.
    assert len(loop_state["iteration_result_chunks"]) == 2
    assert len(loop_state["iteration_results"]) == 1
    assert all(c["file_id"] for c in loop_state["iteration_result_chunks"])


async def test_gate_off_keeps_results_inline():
    driver = _driver(_store(enabled=False), window=3)  # flag off
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    await _accumulate(driver, loop_state, _entries(10))
    assert len(loop_state["iteration_results"]) == 10
    assert "iteration_result_chunks" not in loop_state


async def test_no_store_keeps_results_inline():
    driver = EventDrivenLoopDriver(_resolver(), iteration_results_window=3)  # no store
    loop_state = {"iteration_results": []}
    await _accumulate(driver, loop_state, _entries(10))
    assert len(loop_state["iteration_results"]) == 10


async def test_finalize_flushes_residual_builds_manifest_and_round_trips(monkeypatch):
    store = _store(enabled=True)
    driver = _driver(store, window=3)
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    entries = _entries(7)
    await _accumulate(driver, loop_state, entries)

    # Stub the sync emit so we can exercise the async finalize in isolation.
    monkeypatch.setattr(
        driver, "_emit_loop_completed_and_exit",
        lambda *a, **k: NodeHandlerResult(),
    )
    await driver._finalize_and_emit(
        None, None, None, None, None,
        loop_state, "inv1", [], 7, "all_done", NodeHandlerResult(),
    )

    # residual (1) flushed → 3 chunks; manifest set + is a file_ref.
    assert len(loop_state["iteration_result_chunks"]) == 3
    manifest = loop_state["iteration_results_final"]
    assert is_file_ref(manifest)
    assert manifest["file_id"] == ""  # no main CSV; chunks ARE the data
    assert manifest["row_count"] == 7

    # materialize concatenates all chunks back into the full list, in order.
    out = await store.materialize(manifest)
    assert isinstance(out, list) and len(out) == 7
    assert out == entries


async def test_finalize_small_loop_leaves_results_inline(monkeypatch):
    store = _store(enabled=True)
    driver = _driver(store, window=100)  # high cap → never spills
    loop_state = {"invocation_id": "inv1", "iteration_results": _entries(2)}
    monkeypatch.setattr(
        driver, "_emit_loop_completed_and_exit",
        lambda *a, **k: NodeHandlerResult(),
    )
    await driver._finalize_and_emit(
        None, None, None, None, None,
        loop_state, "inv1", [], 2, "all_done", NodeHandlerResult(),
    )
    assert "iteration_results_final" not in loop_state
    assert loop_state["iteration_results"] == _entries(2)


async def test_manifest_materialize_preserves_nested_and_types():
    store = _store(enabled=True)
    driver = _driver(store, window=2)
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    entries = _entries(5)  # → chunks of 2,2 + residual 1
    await _accumulate(driver, loop_state, entries)
    await driver._spill_window(loop_state)  # flush residual
    manifest = driver._build_chunk_manifest(loop_state["iteration_result_chunks"])

    out = await store.materialize(manifest)
    assert out == entries  # nested item/output dicts + int types round-trip


class _FailingStore:
    """Store whose spill upload always fails — F2 must not lose data."""

    _chunked_enabled = True

    async def store_data(self, key, data):
        raise RuntimeError("upload failed")


async def test_spill_failure_keeps_window_inline_no_data_loss():
    driver = EventDrivenLoopDriver(
        _resolver(), data_store_service=_FailingStore(), iteration_results_window=3,
    )
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    await _accumulate(driver, loop_state, _entries(7))
    # Every spill failed → all entries stay inline (no data loss), no chunks.
    assert len(loop_state["iteration_results"]) == 7
    assert loop_state.get("iteration_result_chunks", []) == []


async def test_loop_output_manifest_composes_with_F1_and_resolve_through():
    # The loop node output {results: manifest} is stored + materialized like any
    # node output: F1 preserves the nested manifest file_ref, and materializing
    # it (what the D2 whole-value resolver does for {{$loop.output.results}})
    # concatenates the chunks back to the full list.
    store = _store(enabled=True)
    driver = _driver(store, window=3)
    loop_state = {"invocation_id": "inv1", "iteration_results": []}
    entries = _entries(7)
    await _accumulate(driver, loop_state, entries)
    await driver._spill_window(loop_state)
    manifest = driver._build_chunk_manifest(loop_state["iteration_result_chunks"])

    stored = await store.store_data("loopnode", {"results": manifest, "count": 7})
    materialized = await store.materialize(stored)
    assert materialized["count"] == 7
    assert is_file_ref(materialized["results"])  # F1 kept the manifest a pointer

    results = await store.materialize(materialized["results"])
    assert results == entries
