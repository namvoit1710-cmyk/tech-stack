"""A file-backed loop collection must survive the row it is stored in.

Measured on tenant-1 on 2026-08-09, run ``33f1818a`` (24 000-row collection):

    LoopStarted            total=10                     <- the loop DID resolve it
    LoopIterationStarted   item={"i":0,"sku":"SKU-0"}   <- items read fine at dispatch
    then the body node:    "Cannot traverse 'i' on NoneType"

``$loop.item`` is not read from the event. ``orchestration_engine.py:2281`` reads
``run.context.loop_state[node]["items"]`` after the run reloads — and the reload
had lost it. ``to_row`` reduced the ref to ``ref["file_id"]``, ``from_row`` was
never given it back, and ``refs_for_run`` (the method that exists to resolve it)
has zero callers in the tree. Same run with a 3-row (inline) collection passes,
which is the control that isolates it to the file-backed path.

``file_id`` alone is not a repairable shortening, and that is the load-bearing
point here: ``materialize_range`` decodes each row through ``column_types`` /
``value_kind`` / ``item_kind``, which live on the **ref**, not in the file. One
CSV decodes to three different values depending on the ref that names it — the
reason ``_loop_item_at`` refuses to memoize by ``file_id``. So the pointer has to
round-trip WHOLE.

Two homes are wrong and one is right:

* ``ITEMS_REF NVARCHAR(128)`` — holds an id, cannot hold a ref.
* ``STATE_JSON NVARCHAR(5000)`` — a ref costs ``392 B + 40 B x columns``
  (measured over 614 refs), so it overflows at ~65 columns and ``save_run`` then
  SKIPS the row, losing the whole loop. A 400-column table is ordinary.
* ``PROGRESS_JSON NCLOB`` — already the home for ``iteration_results`` (bounded
  at 256 KB, i.e. 85x anything here). No cap to trip.

The scale tests below are the ones that matter: the pointer must scale with
COLUMNS only, never with rows, and under ``thin`` it must stop scaling at all.
"""
from __future__ import annotations

import json

import pytest

from app.layer2_application.data.loop_state_rows import (
    STATE_JSON_MAX,
    LoopStateTooLarge,
    from_row,
    to_row,
)
from app.layer2_application.data.schema_dictionary import (
    SchemaDictionary,
    expand,
)
from app.layer1_domain.entities.data_reference import schema_hash


def _ref(columns=("i", "sku"), rows=24_000, file_id="file-abc", hashed=True):
    """A collection pointer of the shape the loop actually stores — a flat,
    artifact-free ref, which is the ONLY shape ``is_lazy_range_eligible``
    admits (``data_store_service.py:860``: ``file_id and not artifacts``)."""
    cols = list(columns)
    types = {c: "string" for c in cols}
    ref = {
        "__file_ref": True,
        "file_id": file_id,
        "content_type": "text/csv",
        "row_count": rows,
        "columns": cols,
        "column_types": types,
        "value_kind": "array",
        "item_kind": "object",
        "artifacts": [],
    }
    if hashed:
        ref["schema_hash"] = schema_hash(cols, types)
    return ref


def _state(**over):
    base = {
        "loop_type": "collection", "mode": "parallel", "total": 10,
        "current_index": 0, "next_dispatch_index": 5, "parallelism": 5,
        "invocation_id": "inv-1", "counter_variable": "index", "condition": "",
        "enclosing_scope_stack": [], "completed_iterations": [],
        "failed_iterations": [],
    }
    base.update(over)
    return base


def _roundtrip(state, **kw):
    """to_row → the columns → from_row, the way save_run/get_run pair them."""
    return from_row(to_row(state, **kw))


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str).encode())


# ───────────────────────── the bug itself ─────────────────────────────


class TestAFileBackedCollectionSurvives:
    def test_items_comes_back_at_all(self):
        """The whole outage in one line. Before the fix this key is absent and
        ``.get("items", [])`` yields ``[]`` → ``item_at([], 4)`` → ``None`` →
        ``$loop.item.i`` → "Cannot traverse 'i' on NoneType"."""
        out = _roundtrip(_state(items=_ref()))
        assert "items" in out

    def test_it_comes_back_whole_not_as_an_id(self):
        """``file_id`` alone cannot decode a row: the same bytes read as
        ``{'a': 1}`` / ``[{'a': 1}]`` / ``[{'a': '1'}]`` depending on
        ``value_kind`` and ``column_types``, which are on the ref."""
        ref = _ref()
        out = _roundtrip(_state(items=ref))
        assert out["items"] == ref

    def test_every_field_materialize_range_needs_is_present(self):
        out = _roundtrip(_state(items=_ref()))
        for key in ("file_id", "row_count", "columns", "column_types",
                    "value_kind", "item_kind"):
            assert key in out["items"], f"{key} lost — rows decode wrong"

    def test_a_manifest_with_an_empty_file_id_still_round_trips(self):
        """``_build_chunk_manifest`` mints ``file_id=""`` on purpose — its
        artifacts ARE the data. The old code stored NULL for it, i.e. dropped
        the manifest entirely."""
        ref = _ref(file_id="")
        out = _roundtrip(_state(iteration_results=ref))
        assert out["iteration_results"] == ref

    def test_the_retired_columns_are_not_used_any_more(self):
        row = to_row(_state(items=_ref()))
        assert row["ITEMS_REF"] is None
        assert row["RESULTS_REF"] is None

    def test_the_ref_does_not_ride_state_json(self):
        """STATE_JSON's cap is the enforcement that keeps payload off the row;
        an unbounded ref must not be what trips it."""
        row = to_row(_state(items=_ref()))
        assert "items" not in json.loads(row["STATE_JSON"])
        assert "items" in json.loads(row["PROGRESS_JSON"])


# ────────────────── the shapes that must NOT change ───────────────────


class TestTheOtherTwoShapes:
    def test_a_small_inline_list_still_rides_state_json(self):
        """``total`` is p50 4 here and ``item_at`` costs one OData call per row
        on a pointer, so small collections must stay inline. Unchanged."""
        row = to_row(_state(items=[{"a": 1}, {"a": 2}]))
        assert json.loads(row["STATE_JSON"])["items"] == [{"a": 1}, {"a": 2}]
        assert "items" not in json.loads(row["PROGRESS_JSON"] or "{}")

    def test_the_index_range_marker_round_trips(self):
        """A count/condition loop carries ``{"__index_range__": n}``, not data.
        It is a dict but not a file_ref, so it must take the inline path."""
        out = _roundtrip(_state(items={"__index_range__": 10}))
        assert out["items"] == {"__index_range__": 10}

    def test_an_inline_results_window_still_rides_progress_json(self):
        row = to_row(_state(iteration_results=[{"r": 1}]))
        assert json.loads(row["PROGRESS_JSON"])["iteration_results"] == [{"r": 1}]

    def test_an_absent_collection_stays_ABSENT_not_none(self):
        """20 call sites do ``loop_state.get("items")``. ``None`` there reads as
        "this loop has no items", which is a different fact from "not loaded"."""
        assert "items" not in _roundtrip(_state())

    def test_an_empty_collection_is_still_legal(self):
        row = to_row(_state(total=0, items=[]))
        assert row["ITEMS_REF"] is None

    def test_an_oversized_inline_list_still_trips_the_cap(self):
        """The claim-check keeps inline lists under ~4 KB; when it fails the
        collection stays inline and the cap is the backstop. Still true."""
        with pytest.raises(LoopStateTooLarge, match="items"):
            to_row({"total": 1, "items": [{"a": "x" * 200} for _ in range(50)]})

    def test_a_legacy_row_holding_a_bare_file_id_reads_as_absent(self):
        """Rows written before this change hold an id in ITEMS_REF and nothing
        in PROGRESS_JSON. Absent is exactly what happens today, so an old row is
        no worse — it must not become a ref-shaped string."""
        out = from_row({"STATE_JSON": json.dumps({"total": 3}),
                        "PROGRESS_JSON": None, "ITEMS_REF": "file-legacy"})
        assert "items" not in out


# ──────────────────────────── at scale ────────────────────────────────


class TestLargeData:
    def test_ten_million_rows_costs_nothing(self):
        """``row_count`` is an integer in the ref and ``total`` is
        ``min(row_count, max_iterations)``. Measured on tenant-1: 100 000-row
        files have a SMALLER pointer than 10-row files, because the correlation
        is with columns."""
        small = to_row(_state(items=_ref(rows=10)))
        huge = to_row(_state(items=_ref(rows=10_000_000)))
        assert abs(len(huge["PROGRESS_JSON"]) - len(small["PROGRESS_JSON"])) < 20

    def test_a_2000_column_collection_does_not_trip_the_cap(self):
        """392 B + 40 B x 2000 ~ 78 KB. In STATE_JSON that raises
        ``LoopStateTooLarge`` and ``save_run`` skips the row — the loop loses
        ALL its state, which is worse than the bug being fixed."""
        cols = tuple(f"col_{i}" for i in range(2000))
        row = to_row(_state(items=_ref(columns=cols)))
        assert len(row["STATE_JSON"].encode()) < STATE_JSON_MAX

    def test_a_2000_column_collection_round_trips_intact(self):
        cols = tuple(f"col_{i}" for i in range(2000))
        ref = _ref(columns=cols)
        out = _roundtrip(_state(items=ref))
        assert out["items"] == ref
        assert len(out["items"]["columns"]) == 2000

    def test_the_pointer_scales_with_columns_and_that_is_the_problem(self):
        """Stated as a test so the coefficient cannot drift unnoticed: this is
        WHY ``thin`` exists. ~40 B per column, measured over 614 live refs."""
        narrow = _size(_ref(columns=tuple(f"c{i}" for i in range(10))))
        wide = _size(_ref(columns=tuple(f"c{i}" for i in range(2000))))
        assert wide > narrow * 20

    def test_thin_stops_it_scaling_with_columns(self):
        """With the schema replaced by its digest the pointer is ~392 B + 0.2 B
        x columns — 93x smaller at 2000 columns, and effectively constant."""
        cols = tuple(f"col_{i}" for i in range(2000))
        fat = to_row(_state(items=_ref(columns=cols)))
        lean = to_row(_state(items=_ref(columns=cols)), thin=True)
        assert len(lean["PROGRESS_JSON"]) * 20 < len(fat["PROGRESS_JSON"])

    def test_thin_is_constant_across_widths(self):
        narrow = to_row(_state(items=_ref(columns=("a", "b"))), thin=True)
        wide = to_row(
            _state(items=_ref(columns=tuple(f"c{i}" for i in range(2000)))),
            thin=True,
        )
        assert abs(len(wide["PROGRESS_JSON"]) - len(narrow["PROGRESS_JSON"])) < 100

    def test_thin_round_trips_through_the_dictionary(self):
        """The digest is only a shortening if the schema can be restored from
        ``AIW_SCHEMA_DEF``. Expanded, it must equal what was stored."""
        cols = tuple(f"col_{i}" for i in range(2000))
        ref = _ref(columns=cols)
        store = {ref["schema_hash"]: {"columns": ref["columns"],
                                      "column_types": ref["column_types"]}}
        out = _roundtrip(_state(items=ref), thin=True)
        assert "columns" not in out["items"]        # stripped, not emptied —
        assert "column_types" not in out["items"]   # `expand` skips a falsy []
        expand(out["items"], SchemaDictionary(lambda ds: {d: store[d] for d in ds
                                                         if d in store}))
        assert out["items"]["columns"] == list(cols)
        assert out["items"]["column_types"] == ref["column_types"]

    def test_thin_declines_when_the_schema_could_not_be_restored(self):
        """A ref with no ``schema_hash`` has nothing to look the schema up by.
        Shrinking it would produce a pointer that decodes with GUESSED types —
        ``"007"`` becomes ``7`` — which is the SA-1977 defect returning."""
        ref = _ref(hashed=False)
        row = to_row(_state(items=ref), thin=True)
        assert json.loads(row["PROGRESS_JSON"])["items"]["columns"] == ref["columns"]

    def test_a_nested_artifact_tree_is_thinned_recursively(self):
        """Not reachable through ``is_lazy_range_eligible`` today, but
        ``_claim_check_items`` stores whatever the store returns and the tree is
        where the 167 KB refs on this tenant get their bulk."""
        child = _ref(columns=tuple(f"k{i}" for i in range(500)), file_id="child")
        parent = _ref(columns=tuple(f"p{i}" for i in range(500)))
        parent["artifacts"] = [child]
        lean = to_row(_state(items=parent), thin=True)
        stored = json.loads(lean["PROGRESS_JSON"])["items"]
        assert "columns" not in stored
        assert "columns" not in stored["artifacts"][0]
        assert stored["artifacts"][0]["schema_hash"] == child["schema_hash"]
