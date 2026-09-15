"""``loop_state`` ⇄ ``AIW_RUN_LOOP_STATE`` mapping.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15, migration 056.

``loop_state`` is the ONE ``RunContext`` field that is neither event-sourced nor
derivable from the workflow, so it is the last thing standing between the
redesign and ``DROP CONTEXT``. These tests pin the three growth classes the
column layout is built on, and the two loud failures that keep the payload from
creeping back into the row.
"""
from __future__ import annotations

import json

import pytest

from app.layer2_application.data.loop_state_rows import (
    INLINE_PAYLOAD_HOME,
    PAYLOAD_KEYS,
    PROGRESS_KEYS,
    SCALAR_KEYS,
    STATE_JSON_MAX,
    LoopStateTooLarge,
    from_row,
    to_row,
)


def _ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "row_count": 3}


def _realistic() -> dict:
    """The shape tenant-1 actually stores, refs substituted for the two payload
    keys (which is what §15.3 fix #1 makes unconditional)."""
    return {
        "loop_type": "collection", "mode": "parallel", "total": 4,
        "current_index": 2, "next_dispatch_index": 3, "parallelism": 2,
        "invocation_id": "inv-1", "counter_variable": "", "condition": "",
        "enclosing_scope_stack": [], "continue_requested": False,
        "break_requested": False, "iteration_result_chunks": [],
        "completed_iterations": [0, 1], "failed_iterations": [],
        "iter_progress": {2: ["b1"]}, "iter_failed_nodes": {},
        "items": _ref("items-file"), "iteration_results": _ref("results-file"),
    }


# ─────────────────── class A — the scalars ride STATE_JSON ────────────


class TestTheScalars:
    def test_they_land_in_state_json(self):
        row = to_row(_realistic())
        state = json.loads(row["STATE_JSON"])
        assert state["total"] == 4
        assert state["mode"] == "parallel"
        assert state["invocation_id"] == "inv-1"

    def test_the_real_shape_is_nowhere_near_the_cap(self):
        """Measured at ~107 B per loop in production. If this ever approaches
        5000 the cap has stopped being headroom and started being a limit."""
        row = to_row(_realistic())
        assert len(row["STATE_JSON"].encode("utf-8")) < STATE_JSON_MAX // 10

    def test_keys_are_sorted_so_an_unchanged_state_does_not_churn(self):
        a = to_row({"total": 1, "mode": "x"})["STATE_JSON"]
        b = to_row({"mode": "x", "total": 1})["STATE_JSON"]
        assert a == b

    def test_total_is_promoted_to_its_own_column(self):
        """Queryable without parsing JSON — 'which loops are still short of
        their total' is the question anyone debugging a stuck loop asks."""
        assert to_row(_realistic())["TOTAL"] == 4

    @pytest.mark.parametrize("bad", [None, "", "x", {}])
    def test_a_junk_total_becomes_zero_not_a_crash(self, bad):
        assert to_row({"total": bad})["TOTAL"] == 0

    def test_an_unrecognised_key_survives(self):
        """Routing is by EXCLUSION. A key added to the drivers tomorrow must
        not vanish into the gap between two allow-lists — silently dropping
        loop state is the exact class of bug this table exists to end."""
        row = to_row({"total": 1, "some_future_key": "keep me"})
        assert json.loads(row["STATE_JSON"])["some_future_key"] == "keep me"


# ─────────────── class B — progress rides its own NCLOB ──────────────


class TestTheProgressKeys:
    def test_they_are_kept_out_of_state_json(self):
        row = to_row(_realistic())
        state = json.loads(row["STATE_JSON"])
        for key in PROGRESS_KEYS:
            assert key not in state
        assert json.loads(row["PROGRESS_JSON"])["completed_iterations"] == [0, 1]

    def test_absent_progress_leaves_the_column_null(self):
        """NULL rather than ``'{}'`` so 'never had progress' and 'progress was
        emptied' stay distinguishable in the table."""
        assert to_row({"total": 1})["PROGRESS_JSON"] is None

    def test_iteration_keys_come_back_as_ints(self):
        """PROGRESS_JSON is JSON, so it drops int keys the same way CONTEXT
        did. The new home must not inherit the old bug — see
        ``test_loop_state_iteration_keys``."""
        row = to_row(_realistic())
        assert json.loads(row["PROGRESS_JSON"])["iter_progress"] == {"2": ["b1"]}
        assert from_row(row)["iter_progress"] == {2: ["b1"]}


# ───────────── class C — the payload never rides the row ─────────────


class TestThePayloadKeys:
    def test_a_ref_is_stored_WHOLE_not_as_an_id(self):
        """Was ``assert row["ITEMS_REF"] == "items-file"``. That assertion
        pinned the defect: ``file_id`` cannot restore a ref, because
        ``materialize_range`` decodes rows through ``column_types`` /
        ``value_kind`` — which live on the ref. See
        ``test_loop_items_ref_roundtrip.py`` for the measured failure."""
        row = to_row(_realistic())
        assert row["ITEMS_REF"] is None and row["RESULTS_REF"] is None
        stored = json.loads(row["PROGRESS_JSON"])
        assert stored["items"] == _ref("items-file")
        assert stored["iteration_results"] == _ref("results-file")

    def test_the_values_are_not_in_state_json(self):
        """These two keys are 99.99% of every byte of loop_state ever written.
        One leak here and the row is the CONTEXT column again."""
        state = json.loads(to_row(_realistic())["STATE_JSON"])
        assert "items" not in state and "iteration_results" not in state

    def test_a_small_inline_items_rides_state_json(self):
        """3 of the 4 measured loops hold ``items`` inline. Dropping them would
        store a NULL ref, which reads as 'this loop has no items' and is
        indistinguishable from data loss. The driver claim-checks the big ones;
        a 2-item loop must not pay an OData fetch per iteration to be storable."""
        row = to_row({**_realistic(), "items": [{"a": 1}, {"a": 2}]})
        assert row["ITEMS_REF"] is None
        assert json.loads(row["STATE_JSON"])["items"] == [{"a": 1}, {"a": 2}]

    def test_an_inline_iteration_results_rides_progress_json(self):
        """It cannot ride ``STATE_JSON`` — the spill gate lets the window reach
        256 KB. It IS bounded, and by a gate that now actually fires, which is
        what ``PROGRESS_JSON`` (NCLOB) exists for."""
        row = to_row({**_realistic(), "iteration_results": [{"r": 1}]})
        assert row["RESULTS_REF"] is None
        assert json.loads(row["PROGRESS_JSON"])["iteration_results"] == [{"r": 1}]
        assert "iteration_results" not in json.loads(row["STATE_JSON"])

    def test_an_oversized_inline_items_still_trips_the_cap(self):
        """The backstop for when the driver's claim-check did not run or
        failed — it keeps the collection inline on a store error."""
        with pytest.raises(LoopStateTooLarge, match="items"):
            to_row({"total": 1, "items": [{"a": "x" * 200} for _ in range(50)]})

    def test_a_ref_never_rides_state_json(self):
        """A ref costs ``392 B + 40 B x columns`` and so overflows
        ``NVARCHAR(5000)`` at ~65 columns — and an overflow makes ``save_run``
        skip the row, losing the whole loop. An inline list has no such tail
        (the claim-check bounds it at ~4 KB), which is why only the ref moved."""
        row = to_row(_realistic())
        assert "items" not in json.loads(row["STATE_JSON"])
        assert "items" in json.loads(row["PROGRESS_JSON"])

    def test_an_absent_payload_is_fine(self):
        row = to_row({"total": 1})
        assert row["ITEMS_REF"] is None and row["RESULTS_REF"] is None

    def test_an_empty_list_is_fine(self):
        """A loop over an empty collection is legal and has nothing to
        claim-check."""
        row = to_row({"total": 0, "items": [], "iteration_results": []})
        assert row["ITEMS_REF"] is None

    def test_a_ref_with_no_file_id_stores_null(self):
        """``_build_chunk_manifest`` mints ``file_id=""`` on purpose — its
        artifacts ARE the data. Storing ``''`` would look like a real id."""
        row = to_row({"total": 1, "iteration_results": _ref("")})
        assert row["RESULTS_REF"] is None


# ──────────────────── the cap is the enforcement ─────────────────────


class TestTheStateJsonCap:
    def test_an_oversized_state_raises(self):
        with pytest.raises(LoopStateTooLarge):
            to_row({"total": 1, "condition": "x" * (STATE_JSON_MAX + 1)})

    def test_the_message_names_the_keys(self):
        """A cap that fires without saying what filled it just moves the
        debugging cost onto whoever is on call."""
        with pytest.raises(LoopStateTooLarge, match="condition"):
            to_row({"condition": "x" * (STATE_JSON_MAX + 1)})

    def test_it_matches_the_column(self):
        assert STATE_JSON_MAX == 5000       # AIW_RUN_LOOP_STATE.STATE_JSON

    def test_it_measures_bytes_not_characters(self):
        """NVARCHAR(5000) is 5000 CHARACTERS in HANA, but a value that fits in
        characters and not in bytes is the wrong thing to wave through — the
        stricter of the two is the safe cap."""
        row = to_row({"condition": "é" * 100})
        assert len(row["STATE_JSON"].encode("utf-8")) > len(row["STATE_JSON"])


# ────────────────────────── the round trip ───────────────────────────


class TestRoundTrip:
    def test_scalars_and_progress_return(self):
        original = _realistic()
        restored = from_row(to_row(original),
                            items=original["items"],
                            iteration_results=original["iteration_results"])
        assert restored == original

    def test_a_stored_payload_now_returns_by_itself(self):
        """Was ``assert "items" not in restored`` — which was true only because
        ``to_row`` had thrown the ref away. The row is now self-sufficient: no
        caller has to pass ``items=`` back in, and none ever did."""
        restored = from_row(to_row(_realistic()))
        assert restored["items"] == _ref("items-file")
        assert restored["iteration_results"] == _ref("results-file")

    def test_a_LEGACY_row_is_still_ABSENT_not_none(self):
        """The distinction still matters, just for a different row: one written
        before this change holds an id in ITEMS_REF and nothing in
        PROGRESS_JSON. 20 call sites read ``loop_state.get("items")``, where
        ``None`` means 'this loop has no items' and absent means 'not loaded'."""
        restored = from_row({"STATE_JSON": json.dumps({"total": 4}),
                             "PROGRESS_JSON": None, "ITEMS_REF": "items-file"})
        assert "items" not in restored
        assert "iteration_results" not in restored

    def test_an_empty_row_yields_an_empty_state(self):
        assert from_row({}) == {}

    @pytest.mark.parametrize("junk", ['{"broken', "", None, "[]", "3"])
    def test_unparseable_json_does_not_raise(self, junk):
        """This is on the load path of every loop. A malformed column has to
        degrade, not take the run down."""
        assert from_row({"STATE_JSON": junk, "PROGRESS_JSON": junk}) == {}

    @pytest.mark.parametrize("junk", [None, [], "x", 3])
    def test_to_row_ignores_a_non_dict(self, junk):
        assert to_row(junk) == {}


# ──────────────────── the classes are exhaustive ─────────────────────


class TestEveryDriverKeyIsAccountedFor:
    """The 20 keys the loop drivers write. Missing one used to mean losing it;
    routing-by-exclusion means it now lands in STATE_JSON instead — this test
    pins WHICH class each known key belongs to, so a key silently changing
    shape (a scalar becoming a payload) is caught here and not in production."""

    DRIVER_KEYS = {
        "break_requested", "completed_iterations", "condition",
        "continue_requested", "counter_variable", "current_index",
        "enclosing_scope_stack", "failed_iterations", "invocation_id", "items",
        "iter_failed_nodes", "iter_progress", "iteration_result_chunks",
        "iteration_results", "iteration_results_final", "loop_type", "mode",
        "next_dispatch_index", "parallelism", "total",
    }

    def test_the_three_classes_partition_them(self):
        classified = set(SCALAR_KEYS) | set(PROGRESS_KEYS) | set(PAYLOAD_KEYS)
        assert self.DRIVER_KEYS == classified

    def test_no_key_is_in_two_classes(self):
        assert not set(SCALAR_KEYS) & set(PROGRESS_KEYS)
        assert not set(SCALAR_KEYS) & set(PAYLOAD_KEYS)
        assert not set(PROGRESS_KEYS) & set(PAYLOAD_KEYS)

    def test_only_the_two_measured_payload_keys_are_payload(self):
        """65% + 35% of all loop_state bytes. Adding a third here without a
        measurement behind it would be guessing."""
        assert set(PAYLOAD_KEYS) == {"items", "iteration_results"}

    def test_every_payload_key_has_an_inline_home(self):
        """A payload key with no fallback home is one that gets silently
        dropped whenever it has not been claim-checked yet."""
        assert set(INLINE_PAYLOAD_HOME) == set(PAYLOAD_KEYS)
        assert set(INLINE_PAYLOAD_HOME.values()) <= {"state", "progress"}
