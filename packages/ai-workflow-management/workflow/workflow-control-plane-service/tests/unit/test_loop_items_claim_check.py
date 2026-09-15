"""An oversized inline ``items`` collection becomes a pointer.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15.3 fix #1.

``items`` is **35% of every byte of ``loop_state`` ever stored** on prod-br10 /
tenant-1 — the other half of the payload, after ``iteration_results`` at 65%.
Three of the four measured loops hold it as a concrete list; one is already a
``file_ref``. Once ``loop_state`` lives in ``AIW_RUN_LOOP_STATE.STATE_JSON
NVARCHAR(5000)``, the fat ones have nowhere to sit.

The threshold is the design, not a detail. ``item_at`` reads a ``file_ref`` one
row at a time through OData, so claim-checking unconditionally would trade zero
HTTP calls for one per iteration on the loop's hot path — and **``total`` is p50
4, max 100 across all 23 loops ever run here**. A blanket rule is wrong in one
direction or the other, so the ceiling is derived from the column that has to
hold the leftovers.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import (
    _LOOP_ITEMS_MAX_INLINE_BYTES,
    EventDrivenLoopDriver,
)


class FakeStore:
    def __init__(self, explode: bool = False, ref: dict | None = None) -> None:
        self.calls: list[tuple[str, list]] = []
        self._explode = explode
        self._ref = ref if ref is not None else {
            "__file_ref": True, "file_id": "items-file", "row_count": 0}

    async def store_data(self, key: str, rows: list):
        if self._explode:
            raise RuntimeError("file service down")
        self.calls.append((key, rows))
        return {**self._ref, "row_count": len(rows)}


def _driver(store=None, **kw):
    return EventDrivenLoopDriver(None, data_store_service=store, **kw)


def _fat(nbytes: int = _LOOP_ITEMS_MAX_INLINE_BYTES * 2) -> list:
    rows, size = [], 0
    while size < nbytes:
        rows.append({"v": "x" * 100})
        size = len(json.dumps(rows).encode())
    return rows


class TestTheCommonCaseStaysInline:
    @pytest.mark.asyncio
    async def test_a_four_item_loop_is_untouched(self):
        """p50 total is 4. Turning this into a pointer would add four OData
        fetches to a loop that needed none."""
        store = FakeStore()
        items = [{"id": i} for i in range(4)]
        assert await _driver(store)._claim_check_items(items, "inv") is items
        assert store.calls == []

    @pytest.mark.asyncio
    async def test_an_empty_collection_is_untouched(self):
        store = FakeStore()
        assert await _driver(store)._claim_check_items([], "inv") == []
        assert store.calls == []

    @pytest.mark.asyncio
    async def test_something_just_under_the_ceiling_stays(self):
        store = FakeStore()
        items = [{"v": "x" * 100}]
        assert len(json.dumps(items).encode()) < _LOOP_ITEMS_MAX_INLINE_BYTES
        assert await _driver(store)._claim_check_items(items, "inv") is items


class TestTheFatCaseBecomesAPointer:
    @pytest.mark.asyncio
    async def test_it_is_stored_and_replaced_by_the_ref(self):
        store = FakeStore()
        out = await _driver(store)._claim_check_items(_fat(), "inv-7")
        assert is_file_ref(out) and out["file_id"] == "items-file"
        assert len(store.calls) == 1

    @pytest.mark.asyncio
    async def test_the_key_carries_the_invocation(self):
        """Two loops in one run must not overwrite each other's collection —
        ``invocation_id`` is what distinguishes them, the same way
        ``_spill_window`` keys its chunks."""
        store = FakeStore()
        await _driver(store)._claim_check_items(_fat(), "inv-7")
        assert store.calls[0][0] == "loop-items-inv-7"

    @pytest.mark.asyncio
    async def test_the_ceiling_is_configurable(self):
        store = FakeStore()
        d = _driver(store, items_max_inline_bytes=10)
        assert is_file_ref(await d._claim_check_items([{"a": 1}, {"b": 2}], "i"))

    @pytest.mark.asyncio
    async def test_the_default_fits_the_column(self):
        """Derived from ``STATE_JSON NVARCHAR(5000)`` minus the ~107 B of
        scalars — not a number picked for its roundness."""
        from app.layer2_application.data.loop_state_rows import STATE_JSON_MAX
        assert _LOOP_ITEMS_MAX_INLINE_BYTES < STATE_JSON_MAX


class TestItNeverStopsALoopFromStarting:
    @pytest.mark.asyncio
    async def test_a_store_failure_keeps_the_collection_inline(self):
        """Same choice ``_spill_window`` makes. A loop that runs with a fat
        ``loop_state`` is a cost; a loop that fails to start is an outage."""
        items = _fat()
        out = await _driver(FakeStore(explode=True))._claim_check_items(items, "i")
        assert out is items

    @pytest.mark.asyncio
    async def test_a_store_that_returns_junk_is_ignored(self):
        """A non-ref return would otherwise replace the collection with
        something ``item_at`` cannot read — every iteration would yield None."""
        items = _fat()
        store = FakeStore(ref={"not": "a ref"})
        assert await _driver(store)._claim_check_items(items, "i") is items

    @pytest.mark.asyncio
    async def test_no_store_wired_is_a_no_op(self):
        """Many test call sites construct the driver with no store at all."""
        items = _fat()
        assert await _driver(None)._claim_check_items(items, "i") is items

    @pytest.mark.asyncio
    async def test_an_unmeasurable_collection_is_stored(self):
        """Fails closed, like ``_window_is_full``: storing something exotic is
        cheap, keeping it on the row is what this exists to stop."""
        class Hostile:
            def __repr__(self):
                raise RuntimeError("nope")

        store = FakeStore()
        out = await _driver(store)._claim_check_items([{"k": Hostile()}], "i")
        assert is_file_ref(out)

    @pytest.mark.asyncio
    async def test_an_existing_ref_passes_straight_through(self):
        """``_coerce_collection`` already returns a pointer for a streamed
        collection (SA-1905 D2.4) — re-storing it would duplicate the file."""
        store = FakeStore()
        ref = {"__file_ref": True, "file_id": "already", "row_count": 9}
        assert await _driver(store)._claim_check_items(ref, "i") is ref
        assert store.calls == []


class TestBothShapesStillRead:
    """The reason this change is two lines at the call site: every read of
    ``items`` already goes through ``item_at`` / ``_items_total``, which handle
    a list and a pointer alike. ``orchestration_engine._loop_item_at`` delegates
    to the same method."""

    @pytest.mark.asyncio
    async def test_item_at_reads_the_inline_list(self):
        assert await _driver(None).item_at([{"a": 1}, {"a": 2}], 1) == {"a": 2}

    @pytest.mark.asyncio
    async def test_items_total_sizes_a_pointer_without_downloading(self):
        d = _driver(None)
        ref = {"__file_ref": True, "file_id": "f", "row_count": 24000}
        assert d._items_total(ref) == 24000
