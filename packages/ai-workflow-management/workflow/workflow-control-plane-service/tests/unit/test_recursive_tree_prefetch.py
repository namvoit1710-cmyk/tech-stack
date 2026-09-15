"""Re-joining a recursive split must not cost one round-trip per file.

``_reconstruct_recursive`` awaited its downloads one at a time — not for any
reason, but because that is the shape a recursive tree walk takes. The whole
set of ``file_id``s is known from the root ref before the first byte moves
(``_store_rows_recursive`` hangs each child's artifacts off its parent), and the
``__parent_row_id`` join needs the bytes, not their arrival order. Measured on a
wide graph: 41 serial round-trips, 2.4s at 50ms each, for 22ms of real work.

The hard part is the SA-1942 byte budget. Its docstring promises peak RAM of
``max_bytes + one chunk``, and that "+ one chunk" held only because the
downloads were serial — a naive ``gather`` would silently turn an OOM guard
into an OOM report. So these tests pin the guard at least as hard as the speed.

Also covered here, because they are the same finding:

* ``_store_rows_recursive`` never stamped ``size_bytes`` (so the guard's
  "declared size, no probe" branch could never fire) or ``columns`` (so the FE
  descriptor for every nested table was blank).
* ``probe_size_bytes`` walked only the TOP level — on a 41-file tree it
  measured 35% of the real bytes, so a 64 MB cap admitted ~180 MB.
* ``is_lazy_range_eligible``'s matrix, which the resolver change moves.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import (
    DataStoreService,
    MaterializeTooLargeError,
    _PREFETCH_CONCURRENCY,
)
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class TracingClient(MockFileServiceClient):
    """Records concurrency, call counts, and can inject faults."""

    def __init__(self, delay: float = 0.0) -> None:
        super().__init__()
        self.delay = delay
        self.downloads: list[str] = []
        self.heads: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self.fail_on: Optional[str] = None

    async def download_file(self, file_id: str) -> Optional[bytes]:
        self.downloads.append(file_id)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_on is not None and file_id == self.fail_on:
                raise RuntimeError("file service exploded")
            return await super().download_file(file_id)
        finally:
            self.in_flight -= 1

    async def head_file(self, file_id: str) -> Optional[dict[str, Any]]:
        self.heads.append(file_id)
        return await super().head_file(file_id)

    def reset(self) -> None:
        self.downloads.clear()
        self.heads.clear()
        self.peak_in_flight = 0


def wide(tables: int = 20, rows: int = 5) -> dict[str, Any]:
    """Distinct content per table so idempotent dedup does not collapse files."""
    return {
        f"tbl{t}": [
            {"id": r, "tag": f"t{t}-{r}", "kids": [{"k": f"{t}-{r}-a"}]}
            for r in range(rows)
        ]
        for t in range(tables)
    }


async def stored(data: Any, delay: float = 0.0, **kw):
    client = TracingClient(delay)
    store = DataStoreService(client, CsvConverter(), recursive_split=True, **kw)
    ref = await store.store_data("k", data)
    client.reset()
    return store, ref, client


# ------------------------------------------------------------ concurrency

async def test_the_tree_is_fetched_concurrently():
    store, ref, client = await stored(wide(), delay=0.01)
    await store.materialize(ref)
    assert client.peak_in_flight > 1, "downloads are still serial — the fix is not live"
    assert client.peak_in_flight <= _PREFETCH_CONCURRENCY


async def test_concurrency_is_bounded_not_unleashed():
    """A wide tree against one file-service instance must not open 200 sockets."""
    store, ref, client = await stored(wide(tables=60, rows=2), delay=0.005)
    await store.materialize(ref)
    assert client.peak_in_flight <= _PREFETCH_CONCURRENCY


async def test_duplicate_file_ids_are_downloaded_once():
    """Byte-identical children dedupe to the same file_id on upload; fetching
    the same bytes N times is pure waste."""
    same = {f"tbl{t}": [{"id": r} for r in range(3)] for t in range(6)}
    store, ref, client = await stored(same)
    await store.materialize(ref)
    assert len(client.downloads) == len(set(client.downloads))


async def test_wall_clock_actually_drops():
    data = wide(tables=16, rows=2)
    store, ref, client = await stored(data, delay=0.02)
    n_files = len(store._tree_nodes(ref))
    assert n_files > _PREFETCH_CONCURRENCY, "fixture too small to prove anything"
    start = asyncio.get_running_loop().time()
    await store.materialize(ref)
    elapsed = asyncio.get_running_loop().time() - start
    serial_floor = n_files * 0.02
    assert elapsed < serial_floor * 0.6, (
        f"{elapsed:.3f}s vs a {serial_floor:.3f}s serial floor over {n_files} files"
    )


# ------------------------------------------------------------ correctness

@pytest.mark.parametrize("data", [
    {"title": "t", "orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]}]},
    {"a": [{"id": 1, "b": [{"id": 10, "c": [{"v": 100}]}]}]},
    {"orders": [{"id": 1, "items": [{"n": 1}]}, {"id": 2}]},
    {"name": "x", "count": 5, "meta": {"w": "W1"}, "tags": ["a"], "rows": [{"r": 1}]},
    wide(tables=4, rows=3),
])
async def test_round_trip_is_unchanged(data):
    store, ref, _c = await stored(data)
    assert await store.materialize(ref) == data


async def test_deep_chain_round_trips():
    def nest(d):
        return [{"leaf": 1}] if d == 0 else [{"id": d, f"L{d}": nest(d - 1)}]
    data = {"top": nest(6)}
    store, ref, _c = await stored(data)
    assert await store.materialize(ref) == data


async def test_a_download_failure_still_propagates():
    """The serial loop let an exception escape. Swallowing it inside a gather
    would turn a file-service outage into silently truncated data."""
    store, ref, client = await stored(wide(tables=3, rows=2))
    client.fail_on = store._tree_nodes(ref)[-1]["file_id"]
    with pytest.raises(RuntimeError, match="exploded"):
        await store.materialize(ref)


async def test_a_missing_child_file_does_not_crash_the_join():
    """download_file returning None (deleted / not yet visible) must degrade,
    not raise — same as the serial loop did."""
    store, ref, client = await stored({"t": 1, "rows": [{"r": 1}]})
    child = ref["artifacts"][0]["file_id"]
    del client._store[child]
    out = await store.materialize(ref)
    assert out["t"] == 1


# ------------------------------------------------------------ the byte budget

async def test_declared_sizes_refuse_before_any_download():
    """Strictly stronger than the old guard, which could only fire once the
    bytes were already in control-plane RAM."""
    store, ref, client = await stored(wide(tables=8, rows=4))
    with pytest.raises(MaterializeTooLargeError):
        await store.materialize(ref, max_bytes=1)
    assert client.downloads == [], "downloaded despite a declared oversize"


async def test_undeclared_sizes_are_still_capped():
    """A ref written before size_bytes was stamped keeps the post-download
    accounting — the guard must not depend on the new field existing."""
    store, ref, client = await stored(wide(tables=8, rows=4))
    for node in store._tree_nodes(ref):
        node["size_bytes"] = 0
    with pytest.raises(MaterializeTooLargeError):
        await store.materialize(ref, max_bytes=1)


async def test_under_the_cap_still_materializes():
    data = {"title": "t", "orders": [{"id": 1, "items": [{"n": 1}]}]}
    store, ref, _c = await stored(data)
    assert await store.materialize(ref, max_bytes=10 * 1024 * 1024) == data


async def test_cap_of_zero_disables_the_guard():
    store, ref, _c = await stored(wide(tables=4, rows=2))
    assert await store.materialize(ref, max_bytes=0) == wide(tables=4, rows=2)


async def test_oversize_stops_scheduling_the_rest():
    """Once the cap is blown the remaining downloads must be abandoned, not
    run to completion for a result that will be thrown away."""
    store, ref, client = await stored(wide(tables=30, rows=2))
    for node in store._tree_nodes(ref):
        node["size_bytes"] = 0
    total = len(store._tree_nodes(ref))
    with pytest.raises(MaterializeTooLargeError):
        await store.materialize(ref, max_bytes=1)
    assert len(client.downloads) < total, (
        f"kept fetching all {total} files after the cap was already exceeded"
    )


# ------------------------------------------------------------ stamped metadata

async def test_every_node_declares_its_size():
    store, ref, _c = await stored(wide(tables=5, rows=3))
    for node in store._tree_nodes(ref):
        assert int(node.get("size_bytes") or 0) > 0, f"unstamped node {node.get('name')}"


async def test_nested_artifacts_declare_their_columns():
    """``build_descriptor_tree`` reads exactly this to tell the FE what a
    nested table holds; it was blank for every recursive-split artifact."""
    store, ref, _c = await stored({"t": 1, "orders": [{"id": 1, "sku": "A"}]})
    art = ref["artifacts"][0]
    assert art["name"] == "orders"
    assert "id" in art["columns"] and "sku" in art["columns"]


async def test_declared_size_matches_the_bytes_actually_stored():
    store, ref, client = await stored(wide(tables=3, rows=2))
    for node in store._tree_nodes(ref):
        raw = client._store.get(node["file_id"])
        assert raw is not None and node["size_bytes"] == len(raw)


# ------------------------------------------------------------ probe_size_bytes

async def test_probe_counts_the_whole_tree_not_just_the_top_level():
    store, ref, client = await stored(wide(tables=10, rows=3))
    probed = await store.probe_size_bytes(ref)
    real = sum(len(v) for v in client._store.values())
    assert probed == real, f"probe saw {probed} of {real} bytes"


async def test_probe_costs_no_round_trips_when_sizes_are_declared():
    store, ref, client = await stored(wide(tables=10, rows=3))
    await store.probe_size_bytes(ref)
    assert client.heads == [], "still HEAD-probing what the ref already declares"


async def test_probe_falls_back_to_head_for_legacy_refs():
    store, ref, client = await stored(wide(tables=4, rows=2))
    nodes = store._tree_nodes(ref)
    for node in nodes:
        node["size_bytes"] = 0
    probed = await store.probe_size_bytes(ref)
    assert len(client.heads) == len({n["file_id"] for n in nodes})
    assert probed == sum(len(v) for v in client._store.values())


async def test_probe_returns_none_when_nothing_is_measurable():
    client = TracingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True)
    assert await store.probe_size_bytes({"not": "a ref"}) is None


async def test_probe_of_an_empty_ref_is_none():
    client = TracingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True)
    assert await store.probe_size_bytes(build_file_ref(file_id="")) is None


async def test_guarded_materialize_refuses_an_oversized_tree():
    """End to end: the recursive probe is what makes the cap real."""
    store, ref, _c = await stored(wide(tables=10, rows=4), materialize_max_bytes=200)
    with pytest.raises(MaterializeTooLargeError):
        await store.materialize_guarded(ref, context="test")


# ------------------------------------------------- event-loop cooperation

async def test_the_rejoin_yields_between_files():
    """Prefetching removed the per-file `await download_file(...)`, and those
    were the only REAL suspension points in the walk — awaiting a coroutine
    that never suspends does not hand the loop a turn. Without explicit yields
    the whole reconstruct becomes one uninterruptible block.

    Counted, not timed, so it cannot flake: a task that re-yields immediately
    must get at least one turn per file plus one before the deep strip.
    """
    store, ref, _c = await stored(wide(tables=6, rows=4))
    files = len(store._tree_nodes(ref))

    turns = 0
    stop = False

    async def ticker():
        nonlocal turns
        while not stop:
            await asyncio.sleep(0)
            turns += 1

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    turns = 0
    out = await store.materialize(ref)
    stop = True
    await t

    assert out == wide(tables=6, rows=4)
    assert turns >= files, (
        f"only {turns} loop turns for {files} files — the re-join stopped "
        "yielding, so it now blocks the event loop for its whole duration"
    )


# ------------------------------- the other two serial loops in materialize

async def flat_stored(data: Any, delay: float = 0.0, **kw):
    """Legacy depth-1 split — ``array_field`` artifacts, not ``nested_array``."""
    client = TracingClient(delay)
    store = DataStoreService(client, CsvConverter(), recursive_split=False, **kw)
    ref = await store.store_data("k", data)
    client.reset()
    return store, ref, client


ARRAY_FIELDS = {
    "header": "H",
    **{f"tbl{t}": [{"x": r, "tag": f"{t}-{r}"} for r in range(4)] for t in range(15)},
}


async def test_array_field_reattach_is_concurrent():
    store, ref, client = await flat_stored(ARRAY_FIELDS, delay=0.01)
    assert len(ref["artifacts"]) == 15
    assert await store.materialize(ref) == ARRAY_FIELDS
    assert client.peak_in_flight > 1, "array_field re-attach is still serial"
    assert client.peak_in_flight <= _PREFETCH_CONCURRENCY


async def test_array_field_declared_oversize_refuses_before_artifact_download():
    """Narrower than the recursive path's guarantee, and deliberately so: this
    branch has to read the MAIN CSV first — it is the dict the artifacts are
    attached to. What the pre-charge buys is that none of the 15 artifact files
    are fetched once the declared total is already over the cap.
    (``materialize_guarded`` still refuses the whole thing with zero downloads,
    via the now-recursive ``probe_size_bytes``.)"""
    store, ref, client = await flat_stored(ARRAY_FIELDS)
    main_id = ref["file_id"]
    with pytest.raises(MaterializeTooLargeError):
        await store.materialize(ref, max_bytes=1)
    assert client.downloads == [main_id], (
        f"fetched artifact files after the cap was already blown: "
        f"{[d for d in client.downloads if d != main_id]}"
    )


async def test_a_broken_array_field_still_only_warns():
    """This path deliberately tolerates a per-artifact failure — the recursive
    one propagates. Batching the downloads must not swap the two policies."""
    store, ref, client = await flat_stored(ARRAY_FIELDS)
    client.fail_on = ref["artifacts"][3]["file_id"]
    out = await store.materialize(ref)
    assert out["header"] == "H"
    broken = ref["artifacts"][3]["name"]
    assert out.get(broken) in (None, [], ARRAY_FIELDS[broken])
    intact = ref["artifacts"][0]["name"]
    assert out[intact] == ARRAY_FIELDS[intact], "one bad artifact took out the others"


async def test_array_field_budget_is_not_double_charged():
    """Declared sizes are pre-charged; the on-arrival add must skip those or a
    value would be refused at half its real cap."""
    store, ref, _c = await flat_stored(ARRAY_FIELDS)
    total = sum(int(a.get("size_bytes") or 0) for a in ref["artifacts"])
    assert total > 0
    assert await store.materialize(ref, max_bytes=10 * 1024 * 1024) == ARRAY_FIELDS


async def test_loop_chunk_manifest_keeps_its_order():
    """Chunks are concatenated; fetching them together must not let arrival
    order become result order."""
    client = TracingClient(delay=0.005)
    store = DataStoreService(client, CsvConverter())
    ids = []
    for i in range(8):
        rows = [{"i": i * 10 + j} for j in range(3)]
        csv_bytes, _fn, _rc = CsvConverter().dict_to_csv(rows)
        up = await client.upload_file(csv_bytes, f"c{i}.csv", "text/csv")
        ids.append(up["file_id"])
    ref = build_file_ref(file_id="", value_kind="array", item_kind="object")
    ref["artifacts"] = [
        {"name": f"c{i}", "file_id": fid, "artifact_type": "loop_chunk",
         "artifacts": []}
        for i, fid in enumerate(ids)
    ]
    client.reset()
    out = await store.materialize(ref)
    assert [r["i"] for r in out] == [i * 10 + j for i in range(8) for j in range(3)]
    assert client.peak_in_flight > 1, "loop_chunk manifest is still serial"


# ------------------------------------------------------------ lazy eligibility

async def test_lazy_eligibility_matrix():
    """Pinned deliberately. Every artifact type here contributes data to the
    ROWS, and ``materialize_range`` re-joins none of them — so "has artifacts"
    is the correct disqualifier, not an over-strict one. Loosening this to make
    more loops lazy silently drops every nested field.

    What DID change is what a loop is handed: ``{{$node.orders}}`` now resolves
    to a pointer at the child, so a child table with no grandchildren is lazy
    again instead of dragging the whole parent tree into RAM.
    """
    async def eligibility(data, path):
        store, ref, client = await stored(data)
        strat = FileBackedResolverStrategy(store, file_client=client)
        target = await strat.try_resolve(ref, path) if path else ref
        return store.is_lazy_range_eligible(target)

    assert await eligibility([{"id": i} for i in range(5)], None) is True
    assert await eligibility({"t": 1, "orders": [{"id": 1}]}, ("orders",)) is True
    assert await eligibility(
        {"t": 1, "orders": [{"id": 1, "items": [{"n": 1}]}]}, ("orders",),
    ) is False
    assert await eligibility([{"id": 1, "items": [{"n": 1}]}], None) is False


async def test_the_eager_fallback_returns_complete_rows():
    """The price of declining lazily must be latency, never missing fields."""
    data = {"t": 1, "orders": [{"id": i, "items": [{"n": i}]} for i in range(4)]}
    store, ref, client = await stored(data)
    strat = FileBackedResolverStrategy(store, file_client=client)
    pointer = await strat.try_resolve(ref, ("orders",))
    assert store.is_lazy_range_eligible(pointer) is False
    assert await store.materialize(pointer) == data["orders"]


async def test_a_lazy_page_equals_the_eager_rows():
    """When the ref IS eligible, the two routes must agree row for row."""
    data = [{"id": i, "sku": f"S{i}"} for i in range(12)]
    store, ref, _c = await stored(data)
    assert store.is_lazy_range_eligible(ref) is True
    eager = await store.materialize(ref)
    for i in range(0, 12, 5):
        assert await store.materialize_range(ref, i, 5) == eager[i:i + 5]
