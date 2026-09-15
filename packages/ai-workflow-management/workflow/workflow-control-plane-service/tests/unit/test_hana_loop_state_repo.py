"""``HanaLoopStateRepository`` — the last blocker on ``DROP CONTEXT``.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13, §15. Tables from
migrations 056 / 057.

``loop_state`` is the ONE ``RunContext`` field that is neither event-sourced nor
derivable from the workflow, so if it has no home the CONTEXT column cannot be
dropped. It is also the field whose loss does not fail loudly: a loop that lost
its state does not error, it hangs.

The fake below is a **row store**, not a dict keyed by the repo's own
assumptions — it holds whatever was inserted and returns it in INSERT order, so
a test that passes because the repo happened to ask in a convenient order fails
here instead.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.data_reference import schema_hash
from app.layer2_application.data import schema_dictionary as sd
from app.layer2_application.data.schema_dictionary import SchemaDictionary
from app.layer4_frameworks.persistence.hana.hana_loop_state_repo import (
    HanaLoopStateRepository,
)


class FakeDb:
    """Minimal ``IDatabaseConnection``. Understands only the three statements
    this repo emits, and deliberately returns rows in INSERT order (SEQ ASC is
    never honoured) so anything depending on the ORDER BY is caught."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.writes: list[tuple[str, tuple]] = []
        self.queries: list[tuple[str, tuple]] = []

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        self.writes.append((sql, params))
        if sql.strip().upper().startswith("DELETE"):
            before = len(self.rows)
            self.rows = [r for r in self.rows if r["RUN_ID"] != params[0]]
            return before - len(self.rows)
        cols = ("RUN_ID", "NODE_ID", "INVOCATION_ID", "SEQ", "STATE_JSON",
                "ITEMS_REF", "RESULTS_REF", "PROGRESS_JSON", "TOTAL", "UPDATED_AT")
        self.rows.append(dict(zip(cols, params)))
        return 1

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        self.queries.append((sql, params))
        # lowercased keys, like the real driver
        return [{k.lower(): v for k, v in r.items()}
                for r in self.rows if r["RUN_ID"] == params[0]]


def _ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "row_count": 2}


def _loop(**over) -> dict:
    base = {
        "loop_type": "collection", "mode": "sequential", "total": 4,
        "current_index": 1, "invocation_id": "inv-a",
        "completed_iterations": [0], "iter_progress": {1: ["body"]},
        "items": _ref("items-a"), "iteration_results": _ref("res-a"),
    }
    base.update(over)
    return base


@pytest.fixture
def repo():
    db = FakeDb()
    return HanaLoopStateRepository(db), db


# ────────────────────────────── write ────────────────────────────────


class TestSaveRun:
    def test_one_row_per_loop_node(self, repo):
        r, db = repo
        assert r.save_run("run-1", {"l1": _loop(), "l2": _loop()}) == 2
        assert len(db.rows) == 2

    def test_the_payload_ref_is_stored_whole_in_progress_json(self, repo):
        """Was ``assert row["ITEMS_REF"] == "items-a"``. The ``*_REF`` columns
        are ``NVARCHAR(128)`` — an id fits, a pointer does not — so they are
        retired to NULL and the ref rides ``PROGRESS_JSON`` (NCLOB) intact."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        row = db.rows[0]
        assert row["ITEMS_REF"] is None and row["RESULTS_REF"] is None
        assert json.loads(row["PROGRESS_JSON"])["items"] == _ref("items-a")
        assert "items" not in json.loads(row["STATE_JSON"])

    def test_total_is_mirrored_into_its_column(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop(total=7)})
        assert db.rows[0]["TOTAL"] == 7

    def test_invocation_id_is_a_column_not_only_json(self, repo):
        """056 keeps it OUT of the key on purpose — a table key richer than the
        runtime key (which is the node id alone) would mint rows nothing can
        address — but it still has to be queryable."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop(invocation_id="inv-z")})
        assert db.rows[0]["INVOCATION_ID"] == "inv-z"

    def test_a_run_replaces_its_previous_rows(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop(), "l2": _loop()})
        r.save_run("run-1", {"l1": _loop(total=9)})
        assert len(db.rows) == 1 and db.rows[0]["TOTAL"] == 9

    def test_a_cleared_loop_state_clears_the_table(self, repo):
        """``reset_test_run`` and ``test_full_workflow`` both call
        ``loop_state.clear()``. A per-loop upsert would leave the old row
        behind, and a stale row here reads as a loop that is still running."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        assert r.save_run("run-1", {}) == 0
        assert db.rows == []

    def test_other_runs_are_untouched(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        r.save_run("run-2", {"l1": _loop()})
        r.save_run("run-1", {"l1": _loop(total=3)})
        assert {row["RUN_ID"] for row in db.rows} == {"run-1", "run-2"}
        assert len(db.rows) == 2

    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_no_run_id_writes_nothing(self, repo, junk):
        r, db = repo
        assert r.save_run(junk, {"l1": _loop()}) == 0
        assert db.writes == []

    @pytest.mark.parametrize("junk", [None, [], "x", 3])
    def test_a_non_dict_loop_state_still_clears(self, repo, junk):
        """Garbage in must not leave the previous rows looking current."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        assert r.save_run("run-1", junk) == 0
        assert db.rows == []


class TestARefusedRowDoesNotTakeTheRunDown:
    """``to_row`` raises when ``STATE_JSON`` would overflow its column — the
    driver's ``_claim_check_items`` is what normally keeps that from happening,
    but it keeps the collection inline when the store is down. This is a SHADOW
    write: it must never fail a run, and must never lose a loop silently."""

    def test_the_offending_loop_is_skipped(self, repo):
        r, db = repo
        assert r.save_run("run-1", {"bad": _loop(condition="x" * 6000)}) == 0
        assert db.rows == []

    def test_an_unclaimchecked_collection_trips_it_too(self, repo):
        """What a store failure during ``_claim_check_items`` leaves behind."""
        r, db = repo
        fat = [{"a": "x" * 200} for _ in range(50)]
        assert r.save_run("run-1", {"bad": _loop(items=fat)}) == 0

    def test_the_other_loops_are_still_written(self, repo):
        r, db = repo
        written = r.save_run(
            "run-1", {"bad": _loop(condition="x" * 6000), "good": _loop()})
        assert written == 1
        assert [row["NODE_ID"] for row in db.rows] == ["good"]

    def test_it_is_logged_at_warning(self, repo, caplog):
        """Silence here would make an unfaithful copy indistinguishable from a
        faithful one — the whole point of a shadow write is the divergence
        signal."""
        r, db = repo
        with caplog.at_level("WARNING"):
            r.save_run("run-1", {"bad": _loop(condition="x" * 6000)})
        assert any("loop_state row skipped" in rec.message for rec in caplog.records)

    def test_a_small_inline_collection_is_NOT_refused(self, repo):
        """The common shape: p50 total is 4. Refusing these would mean the
        mirror captured only the one loop in four that is already a ref."""
        r, db = repo
        assert r.save_run("run-1", {"ok": _loop(items=[{"a": 1}, {"a": 2}])}) == 1


# ───────────────────────────── ordering ──────────────────────────────


class TestInsertionOrderSurvivesTheRoundTrip:
    """LOOP_CONTINUE / LOOP_EXIT scan ``reversed(list(loop_state.items()))`` to
    find the INNERMOST enclosing loop. Lose the order and a BREAK lands on the
    wrong loop — silently."""

    def test_seq_is_written_as_the_position(self, repo):
        r, db = repo
        r.save_run("run-1", {"outer": _loop(), "inner": _loop()})
        assert [(row["NODE_ID"], row["SEQ"]) for row in db.rows] == [
            ("outer", 0), ("inner", 1)]

    def test_the_order_comes_back(self, repo):
        r, db = repo
        r.save_run("run-1", {"outer": _loop(), "middle": _loop(), "inner": _loop()})
        assert list(r.get_run("run-1")) == ["outer", "middle", "inner"]

    def test_it_does_not_depend_on_the_row_order(self, repo):
        """The fake returns rows in INSERT order and never honours ``ORDER BY``.
        Shuffling them here is what a partitioned scan or a plan change does in
        production — the repo re-sorts in Python for exactly this reason."""
        r, db = repo
        r.save_run("run-1", {"outer": _loop(), "middle": _loop(), "inner": _loop()})
        db.rows.reverse()
        assert list(r.get_run("run-1")) == ["outer", "middle", "inner"]

    def test_reversed_still_finds_the_innermost(self, repo):
        """The actual expression from ``loop_continue_handler.py:137``."""
        r, db = repo
        r.save_run("run-1", {"outer": _loop(), "inner": _loop()})
        db.rows.reverse()
        restored = r.get_run("run-1")
        assert next(iter(reversed(list(restored.items()))))[0] == "inner"


# ────────────────────────────── read ─────────────────────────────────


class TestGetRun:
    def test_scalars_and_progress_return(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        restored = r.get_run("run-1")["l1"]
        assert restored["total"] == 4
        assert restored["mode"] == "sequential"
        assert restored["completed_iterations"] == [0]

    def test_iteration_keys_come_back_as_ints(self, repo):
        """``PROGRESS_JSON`` is JSON, so it drops int keys exactly the way
        ``CONTEXT`` did. The new home must not inherit the old bug — see
        ``test_loop_state_iteration_keys``."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        assert r.get_run("run-1")["l1"]["iter_progress"] == {1: ["body"]}

    def test_the_payload_keys_come_back(self, repo):
        """Was ``assert "items" not in restored``, justified by "this layer has
        no file-service client". True, and beside the point — a ref is not a
        file, it is the pointer AT one, and storing it needs no client at all.
        A loop whose ``items`` does not survive this round trip is the outage
        measured on tenant-1 (``Cannot traverse 'i' on NoneType``)."""
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        restored = r.get_run("run-1")["l1"]
        assert restored["items"] == _ref("items-a")
        assert restored["iteration_results"] == _ref("res-a")

    def test_a_legacy_row_still_reads_as_absent(self, repo):
        """Rows written before this change carry an id in ITEMS_REF and nothing
        in PROGRESS_JSON — exactly as unusable as they are today, and no worse.
        Absent, never ``None``: the 20 ``.get("items")`` call sites read ``None``
        as 'this loop has no items'."""
        r, db = repo
        db.rows.append({
            "RUN_ID": "run-legacy", "NODE_ID": "l1", "INVOCATION_ID": "inv-a",
            "SEQ": 0, "STATE_JSON": json.dumps({"total": 4}),
            "ITEMS_REF": "items-a", "RESULTS_REF": "res-a",
            "PROGRESS_JSON": None, "TOTAL": 4, "UPDATED_AT": "",
        })
        restored = r.get_run("run-legacy")["l1"]
        assert "items" not in restored and "iteration_results" not in restored

    def test_an_unknown_run_is_an_empty_dict(self, repo):
        r, _ = repo
        assert r.get_run("nope") == {}

    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_no_run_id_queries_nothing(self, repo, junk):
        r, db = repo
        assert r.get_run(junk) == {}
        assert db.queries == []

    def test_one_query_for_the_whole_run(self, repo):
        """A query per loop would be N round-trips on the run-load path."""
        r, db = repo
        r.save_run("run-1", {"a": _loop(), "b": _loop(), "c": _loop()})
        db.queries.clear()
        r.get_run("run-1")
        assert len(db.queries) == 1


class TestTheThinPointer:
    """The size half. A stored ref costs ``392 B + 40 B x columns`` (measured
    over 614 live refs), so a 2 000-column table is a 78 KB pointer rewritten on
    every save — ``save_run`` is delete-then-insert and a loop saves 2-3 times
    per iteration. Replacing the schema with the ``schema_hash`` that names it in
    ``AIW_SCHEMA_DEF`` removes the column term.

    The safety property under test is the ASYMMETRY: thin is written only when
    this repo can expand it. A hash with no entry decodes with guessed types."""

    WIDE = tuple(f"col_{i}" for i in range(2000))

    def _wide_ref(self):
        types = {c: "string" for c in self.WIDE}
        return {"__file_ref": True, "file_id": "wide", "row_count": 9,
                "columns": list(self.WIDE), "column_types": types,
                "value_kind": "array", "item_kind": "object", "artifacts": [],
                "schema_hash": schema_hash(list(self.WIDE), types)}

    def _dictionary(self, ref):
        entry = {"columns": ref["columns"], "column_types": ref["column_types"]}
        return SchemaDictionary(
            lambda ds, e=entry, h=ref["schema_hash"]: {d: e for d in ds if d == h}
        )

    def test_without_a_dictionary_the_ref_is_stored_FAT(self, repo, monkeypatch):
        """No expander wired ⇒ no shrinking, even with the mode on. Storing a
        hash nothing can resolve trades an outage for silent type corruption."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        r, db = repo
        ref = self._wide_ref()
        r.save_run("run-1", {"l1": _loop(items=ref)})
        assert json.loads(db.rows[0]["PROGRESS_JSON"])["items"]["columns"]

    def test_with_the_mode_off_the_ref_is_stored_FAT(self, repo, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        ref = self._wide_ref()
        db = FakeDb()
        r = HanaLoopStateRepository(db, schema_dictionary=self._dictionary(ref))
        r.save_run("run-1", {"l1": _loop(items=ref)})
        assert json.loads(db.rows[0]["PROGRESS_JSON"])["items"]["columns"]

    def test_armed_it_shrinks_the_row_by_more_than_20x(self, monkeypatch):
        ref = self._wide_ref()
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        fat_db = FakeDb()
        HanaLoopStateRepository(fat_db).save_run("r", {"l1": _loop(items=ref)})
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        lean_db = FakeDb()
        HanaLoopStateRepository(
            lean_db, schema_dictionary=self._dictionary(ref),
        ).save_run("r", {"l1": _loop(items=ref)})
        assert (len(lean_db.rows[0]["PROGRESS_JSON"]) * 20
                < len(fat_db.rows[0]["PROGRESS_JSON"]))

    def test_and_get_run_hands_back_the_SAME_ref(self, monkeypatch):
        """The whole point: smaller on disk, identical in memory."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        ref = self._wide_ref()
        db = FakeDb()
        r = HanaLoopStateRepository(db, schema_dictionary=self._dictionary(ref))
        r.save_run("run-1", {"l1": _loop(items=ref)})
        assert r.get_run("run-1")["l1"]["items"] == ref

    def test_a_fat_row_is_untouched_by_expansion(self, monkeypatch):
        """Rows are rewritten only when their run saves, so both shapes live in
        the table at once — permanently, not until a flag flips."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        ref = self._wide_ref()
        db = FakeDb()
        r = HanaLoopStateRepository(db, schema_dictionary=self._dictionary(ref))
        r.save_run("run-1", {"l1": _loop(items=ref)})
        assert r.get_run("run-1")["l1"]["items"] == ref

    def test_a_dictionary_outage_does_not_fail_the_load(self, monkeypatch):
        """Degrade to "the ref keeps what it carries", never to a failed run
        load — the same contract the mirror write has."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        ref = self._wide_ref()

        def boom(_digests):
            raise RuntimeError("AIW_SCHEMA_DEF unreachable")

        db = FakeDb()
        r = HanaLoopStateRepository(db, schema_dictionary=self._dictionary(ref))
        r.save_run("run-1", {"l1": _loop(items=ref)})
        broken = HanaLoopStateRepository(db, schema_dictionary=SchemaDictionary(boom))
        restored = broken.get_run("run-1")["l1"]
        assert restored["items"]["file_id"] == "wide"
        assert "columns" not in restored["items"]


class TestRefsForRunIsGone:
    """``refs_for_run`` existed so a caller could resolve ITEMS_REF / RESULTS_REF
    into real values. It had **zero callers** in the tree, and its design could
    not have worked: a ``file_id`` names bytes, and the same bytes decode to
    three different values depending on ``column_types`` / ``value_kind`` — which
    are on the ref it discarded. Storing the ref whole removes the question."""

    def test_it_is_not_reachable(self, repo):
        r, _ = repo
        assert not hasattr(r, "refs_for_run")


class TestDeleteRun:
    def test_it_removes_only_that_run(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        r.save_run("run-2", {"l1": _loop()})
        r.delete_run("run-1")
        assert [row["RUN_ID"] for row in db.rows] == ["run-2"]

    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_no_run_id_deletes_nothing(self, repo, junk):
        r, db = repo
        r.save_run("run-1", {"l1": _loop()})
        db.writes.clear()
        r.delete_run(junk)
        assert db.writes == []


# ────────────────────────── column truncation ────────────────────────


class TestTheColumnsCannotOverflow:
    def test_a_long_node_id_is_truncated(self, repo):
        """NVARCHAR(128). Node ids are uuids today, but nothing validates the
        length — an overflow raises HANA 274 and would take the SAVE down."""
        r, db = repo
        r.save_run("run-1", {"n" * 500: _loop()})
        assert len(db.rows[0]["NODE_ID"]) == 128

    def test_a_long_invocation_id_is_truncated(self, repo):
        r, db = repo
        r.save_run("run-1", {"l1": _loop(invocation_id="i" * 500)})
        assert len(db.rows[0]["INVOCATION_ID"]) == 64
